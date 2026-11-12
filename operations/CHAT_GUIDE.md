# 在 Codex 聊天中操作商品运营助手

项目位置：`/Users/your-name/Desktop/咸鱼/xianyu-auto-reply`。日常工作通过 `operations/cli.py` 调用已经部署的相同服务与任务数据。

当用户给出商品关键词并要求研究、采集或制作商品时，直接创建任务：

```sh
python3 operations/cli.py start '用户提供的商品关键词'
```

记录返回的任务 ID。用户给出素材路径后：

```sh
python3 operations/cli.py assets TASK_ID '用户提供的完整素材目录'
```

素材可以在研究过程中提前提交。路径限定在当前配置的桌面或文档目录；提交前核对这是用户明确指定的商品素材目录。

```sh
python3 operations/cli.py status TASK_ID
python3 operations/cli.py resume TASK_ID
python3 operations/cli.py cancel TASK_ID
python3 operations/cli.py download TASK_ID '当前聊天的outputs目录'
```

使用 `list` 查找已有任务以便继续，避免重复采集。使用 `--request-key` 为同一创建请求保留稳定标识。`chat` 子命令可发送当前支持的中文任务指令。

任务终态包括 `waiting_assets`（等素材）、`ready`（商品包完成）、`failed`、`cancelled`。终态之前继续跟进当前任务，用阶段和具体结果解释进度；采集或模型调用耗时期间保持简短更新。

报告中明确样本范围、实际有效排名数量与缺失详情。第一版交付到商品草稿；是否已经保存素材、是否已经发布，以任务记录和平台结果分别核对。真实素材尚未提供时，说明缺少的输入，已完成的研究继续保留。

使用常规文件操作和工具参数传递数据，避免将关键词、目录、文案或密钥直接拼进可执行的 shell 代码。交付给用户的报告放在当前聊天的 `outputs/`，项目源代码保持在桌面的项目目录。
