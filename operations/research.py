"""Small resumable analysis units; this layer owns retries and request deadlines."""
import asyncio
from operations import intelligence as ai

UNIT_TIMEOUT = 100
DEFAULT_BATCH_SIZE = 2


async def analyze(config, keyword, samples, result, save):
    batches=result.setdefault('analysis_batches',[])
    # Old releases used contiguous groups of ten without explicit coverage IDs.
    offset=0
    for batch in batches:
        if '_sample_ids' not in batch:
            batch['_sample_ids']=[str(item['item_id']) for item in samples[offset:offset+10]]
        offset+=len(batch['_sample_ids'])
    completed={str(item_id) for batch in batches for item_id in batch['_sample_ids']}
    pending=[item for item in samples if str(item['item_id']) not in completed]
    size=max(1,min(5,int(result.get('analysis_batch_size',DEFAULT_BATCH_SIZE))))
    while pending:
        unit=pending[:size]
        for attempt in range(2):
            await save(stage=f"分析竞品文案 {len(samples)-len(pending)+len(unit)}/{len(samples)}")
            try:
                analysis=await asyncio.wait_for(ai.analyze_batch(config,keyword,unit),timeout=UNIT_TIMEOUT)
            except (asyncio.TimeoutError,ai.AiCallError,ai.IncompleteModelOutput) as exc:
                if isinstance(exc,ai.AiCallError) and not exc.retryable:
                    raise ValueError('AI竞品分析接口返回配置或服务错误，请检查模型配置后继续；采集结果已保留') from exc
                if attempt:
                    reason='响应超时' if isinstance(exc,asyncio.TimeoutError) or '超时' in str(exc) else '暂未完成'
                    raise ValueError(f'AI竞品分析接口{reason}，缩小批次重试后仍未完成。已保存 {len(batches)} 批分析和全部采集商品，可从当前进度继续') from exc
                size=max(1,len(unit)//2)
                result['analysis_batch_size']=size
                await save()
                unit=pending[:size]
                continue
            analysis['_sample_ids']=[str(item['item_id']) for item in unit]
            batches.append(analysis)
            completed.update(analysis['_sample_ids'])
            pending=[item for item in pending if str(item['item_id']) not in completed]
            result['analysis_batch_size']=size
            await save()
            break
    return batches
