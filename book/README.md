# 《AI Native 工程实战：从零到 AI Native 工程师/架构师》

这是一本基于 [`ai-native-framework`](../README.md) 这个真实开源项目写成的书。全书不讲空洞的概念，每一章都对应框架里一个具体的、可以在你自己电脑上跑起来的机制，很多章节还会带你重现一个曾经在真实生产项目里出现过、后来被修复的真实bug。

**这本书适合谁**：想转型/入行 AI Native 工程师、AI Agent 架构师的人，不要求你已经很懂AI，但假设你有基础的Python阅读能力（能看懂`if`/`for`/函数定义）。看不懂的Python语法，书里会随手解释。

**怎么用这本书**：强烈建议每章都打开对应的代码文件、跟着敲一遍命令，而不是只读文字。每章末尾都有"动手做"和"面试可能会问"两个小节。

## 目录

### 第零部分：为什么要学 AI Native
- [ch01 —— 什么是"AI Native"](ch01_什么是AI_Native.md)
- [ch02 —— 全书地图：13个包，13种生产事故](ch02_全书地图.md)

### 第一部分：地基 —— ainative-core
- [ch03 —— Protocol而非继承](ch03_Protocol而非继承.md)
- [ch04 —— 跨厂商模型工厂与降级链](ch04_跨厂商模型工厂与降级链.md)
- [ch05 —— 别名bug大家族](ch05_别名bug大家族.md)
- [ch06 —— 诚实地报告"不知道"](ch06_诚实地报告不知道.md)

### 第二部分：护栏 —— ainative-guardrail
- [ch07 —— Agent会失控](ch07_Agent会失控.md)
- [ch08 —— 模型路由中间件](ch08_模型路由中间件.md)
- [ch09 —— 连续失败与卡死检测](ch09_连续失败与卡死检测.md)
- [ch10 —— 幂等键管理](ch10_幂等键管理.md)
- [ch11 —— 背压与限流](ch11_背压与限流.md)

### 第三部分：Prompt工程化 —— ainative-prompt
- [ch12 —— Prompt是需要版本管理的资产](ch12_Prompt是需要版本管理的资产.md)
- [ch13 —— 确定性哈希粘性路由](ch13_确定性哈希粘性路由.md)
- [ch14 —— LLM-as-Judge](ch14_LLM-as-Judge.md)

### 第四部分：安全 —— ainative-security
- [ch15 —— PII脱敏](ch15_PII脱敏.md)
- [ch16 —— 输出安全扫描](ch16_输出安全扫描.md)
- [ch17 —— Secret Drift监控](ch17_Secret_Drift监控.md)

### 第五部分：治理门禁 —— ainative-eval
- [ch18 —— FCARS门禁](ch18_FCARS门禁.md)
- [ch19 —— 边界复核防噪声](ch19_边界复核防噪声.md)
- [ch20 —— 评判聚合与公平性](ch20_评判聚合与公平性.md)

### 第六部分：记忆 —— ainative-memory
- [ch21 —— Checkpoint持久化](ch21_Checkpoint持久化.md)
- [ch22 —— 长期记忆的存取与裁剪](ch22_长期记忆的存取与裁剪.md)
- [ch23 —— 落盘前的脱敏代理](ch23_落盘前的脱敏代理.md)

### 第七部分：工具协议 —— ainative-mcp
- [ch24 —— MCP是什么](ch24_MCP是什么.md)
- [ch25 —— 环境变量白名单](ch25_环境变量白名单.md)

### 第八部分：编排 —— ainative-workflow
- [ch26 —— 什么时候需要一个图](ch26_什么时候需要一个图.md)
- [ch27 —— 拓扑排序与Kahn算法](ch27_拓扑排序与Kahn算法.md)
- [ch28 —— HITL：人在回路中](ch28_HITL人在回路中.md)

### 第九部分：多智能体协作 —— ainative-a2a
- [ch29 —— 能力注册与发现](ch29_能力注册与发现.md)
- [ch30 —— 委派链路的死循环防护](ch30_委派链路的死循环防护.md)

### 第十部分：脚手架 —— ainative-cli
- [ch31 —— 一条命令生成一个项目](ch31_一条命令生成一个项目.md)
- [ch32 —— 项目名校验与模板系统](ch32_项目名校验与模板系统.md)

### 第十一部分：可观测性 —— ainative-observability
- [ch33 —— 结构化日志](ch33_结构化日志.md)
- [ch34 —— 追踪Span](ch34_追踪Span.md)

### 第十二部分：多租户 —— ainative-tenancy
- [ch35 —— 租户身份传播](ch35_租户身份传播.md)
- [ch36 —— 配额管理](ch36_配额管理.md)
- [ch37 —— 结构性禁止忘记加租户过滤](ch37_结构性禁止忘记加租户过滤.md)

### 第十三部分：RAG —— ainative-rag
- [ch38 —— RAG到底是什么](ch38_RAG到底是什么.md)
- [ch39 —— 分块与混合检索](ch39_分块与混合检索.md)
- [ch40 —— 重排序与诚实的默认值](ch40_重排序与诚实的默认值.md)
- [ch41 —— 缓存与新鲜度](ch41_缓存与新鲜度.md)

### 第十四部分：把13个包拼起来 —— 综合实战
- [ch42 —— flagship_support_platform 端到端走查](ch42_flagship端到端走查.md)
- [ch43 —— multi_tenant_saas_platform](ch43_multi_tenant_saas_platform.md)
- [ch44 —— rag_knowledge_base_service](ch44_rag_knowledge_base_service.md)
- [ch45 —— 你自己的项目](ch45_你自己的项目.md)

### 第十五部分：求职冲刺
- [ch46 —— AI Native工程师/架构师，面试官到底在考什么](ch46_面试官到底在考什么.md)
- [ch47 —— 系统设计题实战](ch47_系统设计题实战.md)
- [ch48 —— 代码题实战](ch48_代码题实战.md)
- [ch49 —— 你的作品集](ch49_你的作品集.md)
