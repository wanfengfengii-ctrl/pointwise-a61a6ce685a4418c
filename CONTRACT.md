# 公告采集器的现有业务契约

本仓库只处理 GET 的 200/304。远端录制是测试替身，业务入口为
await Collector(http, path=':memory:').collect(url, headers)。
http 提供 await get(url, headers) -> transport.Response(status, headers, body)。
path 也可传 SQLite 文件路径；close() 关闭连接。Collector 的同一实例允许多个协程交错调用；
范围不包含多进程、多实例共享写、迁移、缓存过期、重定向及其他状态码。

## 输入与输出

url 是非空字符串。请求头名大小写不敏感，Accept-Language 必须是 en 或 zh；
调用方不提供条件头。传输层接收小写头名。每次调用都发 GET：
有可复用表示且存在 ETag 时发送它的 If-None-Match，否则无条件请求。
不得为了绕过问题禁用缓存，也不得在没有冲突时额外 GET。
源在收到条件头时可以返回 200；没有选中表示的 304 为 UNSOLICITED_304。
非法语言为 LANGUAGE；非法 URL 为 URL；不支持的状态为 STATUS，均为 ValueError。
这些错误不得留下新缓存或提取记录。源异常原样传播。

源正文是 HTML 字符串，不涉及字节流解码。响应头名大小写不敏感，
行为相关字段是 ETag、Content-Language、Vary、Cache-Control。
合法 Vary 仅为空、Accept-Language、*，忽略字段名大小写及首尾空白；
200 缺失 Vary 等同于空，304 缺失则继承。Cache-Control 仅缺失或 no-store，
大小写及首尾空白不敏感。合法录制中的 304 没有正文；它的 ETag 若出现，
与发出请求时的验证器相同。200 可以换验证器；ETag 是不排序、不解析的字符串。
304 只用实际选中表示的正文；它携带的响应头替换同名头，未携带的头保留，
含 Cache-Control、Content-Language、Vary、ETag。

collect 返回字典，固定字段：
title：所有 title 元素的文本拼接（沿用 extract.py）；
body：提取使用的完整正文；
etag、content_language：合并后响应元数据，缺失为 None；
origin_status：本次最终接受的响应状态 200 或 304。
cache.publications() 返回按 seq 排序的已发布记录，包括以上字段及
seq、url、language（本次请求语言）；每次成功调用恰好追加一条。
cache.entries() 返回当前可复用表示，含 id、url、variant、body、headers。
variant 为 en、zh 或空字符串（所有语言）；id 不属于业务身份或可排序源版本。
可增加内部状态，不能改变上述字段的含义。publications 是业务输出，不能充当正文历史缓存。

## 表示的范围与响应竞争

空 Vary 选择范围为该 URL 的两种语言；Accept-Language 只选择本次请求语言。
当前同一 URL 的可复用表示选择范围不得重叠。
Vary:*、no-store 的响应仍可提取和发布，但不能存为可复用表示；
它对现有表示的失效范围按合并后 Vary 确定，* 在此取两种语言。
接受响应时，失效本次原先选中的表示，以及与新范围相交的所有旧表示；
可缓存的新响应按其新范围保存。这也适用于 304 改变 Vary；
不保留旧的广域副本给另一语言继续验证。

请求发出时确定的表示必须保持同一版本，直到响应被接受；
请求后曾被改写或失效再重建，即使正文或 ETag 相同，也已不是原版本。
空缓存请求同样不能倒灌覆盖请求发出后才发布的重叠表示。
对于一个响应，原先选中表示的范围与合并后响应的新范围，合称本次涉及范围。
若请求发出后，其他成功发布改变了本次涉及范围的任何语言，必须丢弃该响应，
重新选择缓存并 GET；丢弃的响应不得追加 publications 或修改缓存。
no-store、Vary:* 的成功发布也会改变范围；不能因未留缓存而忽略。
只改变不相交语言的发布不构成冲突，不得让另一语言额外重试。
源合法且干扰最终停止时调用须完成，不用固定重试上限提前报错。
这只是两个固定语言的乐观发布规则，不要求实现通用 HTTP 缓存引擎。

缓存失效、写入和最终提取记录一起提交；提取异常时全部回滚。
同址请求须能并行，不能用覆盖 GET 等待的锁或队列串行化同址或同语言请求。
不能在 await http.get 的等待期间持有 SQLite 写事务。
被丢弃的 304 不能把新正文与旧头拼成成功结果；被丢弃的 200 也不能倒灌。

## 已有入口与样例

python3 checks.py normal
核对单语言首次 200、304 继承、200 更新及提取失败回滚，输出 NORMAL_REGRESSION_OK。

python3 checks.py incident
依次采 en、zh、en 的 304、zh 的 304。第三次应提取 River notice，
ETag 为 "e1"，Content-Language 为 en，而不是河道公告。
旧基线退出 1 并输出 CACHE_REPRESENTATION_MISMATCH；修复后退出 0，
输出 CACHE_REPRESENTATION_OK。该命令可独立连续重复，不依赖上次运行。

python3 checks.py verify
通过原业务断言及公开小样例，输出 COLLECTOR_VERIFY_OK。
可向此入口补边界回归，不得删除或弱化原断言。

python3 checks.py demo
逐次打印 samples.bilingual 的真实结果及 SQLite publications，
用于观察首次采集和随后重验证的提取内容；它不把演示成功当正确性证明。
