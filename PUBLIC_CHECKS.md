# 可选快速自检

在根目录运行以下任一命令：
- python3 check_public.py languages
- python3 check_public.py vary
- python3 check_public.py interleave

三个样例均调用 Collector.collect，分别核对：
同网址双语各自 304 的标题/验证器/内容语言；
通用表示经 304 收窄为英文后，中文请求不能携带英文验证器；
英文慢 304 被后发的 200 超越后，须重新选择并重验证新正文，丢弃响应不入账。

旧基线三项均退出 1，标记依次为 PUBLIC_LANGUAGES_MISMATCH、
PUBLIC_VARY_MISMATCH、PUBLIC_INTERLEAVE_MISMATCH。
修好对应行为后退出 0，标记后缀为 _OK，打印实际业务结果。
用事件控制交错，不使用网络或计时猜测。普通业务不符由 BehaviorMismatch 报告，
录制配置错误、导入失败等不会冒充预期业务失败。

这些是很小的公开例子，合计通常不足一秒，各项有 15 秒防挂起上限，总计不超过 45 秒。
通过仅说明这些有限样例通过，不代表已覆盖 Vary 扩大、200 迟到、
no-store、Vary:*、不相交语言、失败回滚等完整契约。
正常回归及双次事故复现仍分别使用 checks.py normal / incident。
