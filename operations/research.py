"""Small resumable analysis units; this layer owns retries and request deadlines."""
import asyncio
from operations import intelligence as ai

UNIT_TIMEOUT = 100
DEFAULT_BATCH_SIZE = 2
DEFAULT_OUTPUT_TOKENS = 1800


def select_samples(ranking, parameters):
    limit = parameters.get('research_limit', 100)
    selected = ranking['comparable_hot'] or ranking['hot100']
    controls = [i for i in ranking['controls'] if parameters.get('product_kind') == 'all'
                or i['kind'] == parameters.get('product_kind', 'source')]
    return list({i['item_id']:i for i in selected + controls}.values())[:limit]


def scoped_batches(batches, samples):
    allowed = {str(item['item_id']) for item in samples}
    return [batch for batch in batches if batch.get('_sample_ids')
            and set(map(str, batch['_sample_ids'])).issubset(allowed)]


def failure_reason(exc):
    if isinstance(exc, ai.IncompleteModelOutput):
        return ('output_truncated', '输出达到长度上限，JSON被截断') if exc.finish_reason == 'length' else ('invalid_json', '返回的JSON不完整或缺少必要分析字段')
    if isinstance(exc, asyncio.TimeoutError) or '超时' in str(exc):
        return 'timeout', '响应超时'
    status = getattr(exc, 'status_code', None)
    if status == 429:
        return 'rate_limit', '触发接口限流（HTTP 429）'
    if status:
        return 'http_error', f'返回HTTP {status}'
    return 'connection', '连接或响应异常'


async def analyze(config, keyword, samples, result, save):
    batches=result.setdefault('analysis_batches',[])
    # Old releases used contiguous groups of ten without explicit coverage IDs.
    offset=0
    for batch in batches:
        if '_sample_ids' not in batch:
            batch['_sample_ids']=[str(item['item_id']) for item in samples[offset:offset+10]]
        offset+=len(batch['_sample_ids'])
    active = scoped_batches(batches, samples)
    completed={str(item_id) for batch in active for item_id in batch['_sample_ids']}
    pending=[item for item in samples if str(item['item_id']) not in completed]
    size=max(1,min(5,int(result.get('analysis_batch_size',DEFAULT_BATCH_SIZE))))
    budget=max(DEFAULT_OUTPUT_TOKENS,min(7200,int(result.get('analysis_output_tokens',DEFAULT_OUTPUT_TOKENS))))
    while pending:
        unit=pending[:size]
        for attempt in range(2):
            await save(stage=f"分析竞品文案 {len(samples)-len(pending)+len(unit)}/{len(samples)}")
            try:
                options = {'max_tokens':budget} if budget != DEFAULT_OUTPUT_TOKENS else {}
                analysis=await asyncio.wait_for(ai.analyze_batch(config,keyword,unit,**options),timeout=UNIT_TIMEOUT)
            except (asyncio.TimeoutError,ai.AiCallError,ai.IncompleteModelOutput) as exc:
                code,reason = failure_reason(exc)
                result['analysis_error'] = {'code':code, 'message':reason, 'completed_samples':len(completed),
                                            'target_samples':len(samples), 'output_tokens':budget,
                                            'status_code':getattr(exc,'status_code',None)}
                if isinstance(exc,ai.AiCallError) and not exc.retryable:
                    raise ValueError(f'AI竞品分析接口{reason}，请检查模型配置后继续；采集结果已保留') from exc
                if attempt:
                    retry = '增加输出额度' if code == 'output_truncated' else '缩小批次'
                    raise ValueError(f'AI竞品分析接口{reason}，{retry}重试后仍未完成。已保存 {len(active)} 批分析（{len(completed)}/{len(samples)}件）和全部采集商品，可从当前进度继续') from exc
                if code == 'output_truncated':
                    budget=min(budget*2,7200)
                    result['analysis_output_tokens']=budget
                else:
                    size=max(1,len(unit)//2)
                result['analysis_batch_size']=size
                await save()
                unit=pending[:size]
                continue
            analysis['_sample_ids']=[str(item['item_id']) for item in unit]
            batches.append(analysis)
            active.append(analysis)
            completed.update(analysis['_sample_ids'])
            pending=[item for item in pending if str(item['item_id']) not in completed]
            result['analysis_batch_size']=size
            result.pop('analysis_error',None)
            await save()
            break
    result.pop('analysis_error',None)
    result['analysis_scope']={'sample_ids':[str(item['item_id']) for item in samples],
                              'completed_samples':len(completed),'batch_count':len(active)}
    await save()
    return active
