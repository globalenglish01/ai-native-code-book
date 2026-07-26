# 第14章 —— LLM-as-Judge

代码位置：`packages/ainative-prompt/src/ainative_prompt/judge.py`

## 用AI评判AI，靠不靠谱

假设你想自动判断"这个客服Agent的回复，是不是真的礼貌地道歉了"——这种语义层面的判断，传统的规则匹配（比如"检查文本里有没有'抱歉'这个词"）很容易漏判或者误判。一个常见的思路是：**再调用一次大模型，让它扮演"评判者"的角色，给出0到1之间的一个分数**。这就是"LLM-as-Judge"。

但这里有一个显而易见的问题：大模型的输出本身带有随机性，同一个评判请求，问两次可能得到不完全一样的分数。如果只问一次就把结果当成"标准答案"，这个"标准答案"本身的可信度是存疑的。`ainative-prompt`的`judge_response`函数用了一套很朴素但有效的手段来应对这个问题：**独立调用好几次，取中位数，并且衡量"这几次意见分歧有多大"**。

## 核心流程

```python
async def judge_response(model, prompt, target_response, expected_criteria, *, judge_count=3, sanitize_input=None):
    # int(judge_count)先转成整数；min(..., 5)确保不超过5；
    # max(1, ...)确保不低于1——三层包裹把调用方传的任何离谱数字，
    # 都强制约束到[1, 5]这个安全区间内。
    judge_count = max(1, min(int(judge_count), 5))
    ...
    judge_calls = []   # 收集每一次评判调用的原始结果
    for i in range(judge_count):
        # await model.ainvoke(...)——真正异步调用一次评判模型，
        # 等它返回结果才继续往下走。
        result = await model.ainvoke(messages)
        parsed = _parse_judge_output(result.content)   # 尝试把返回文本解析成结构化数据
        judge_calls.append({"index": i, "raw_output": raw_text, "parsed": parsed})

    # 列表推导式：只保留parsed解析成功（不是None/空）的那些评判结果，
    # 取出里面的score字段，组成一份"有效分数"列表。
    valid_scores = [jc["parsed"]["score"] for jc in judge_calls if jc["parsed"]]
    if not valid_scores:
        # 一次都没能成功解析出分数——诚实地返回失败，而不是硬凑一个假分数。
        return {"ok": False, "reason": "...", "judge_calls": judge_calls}

    median_score = statistics.median(valid_scores)   # 取中位数，不容易被极端值带偏
    # 只有多于1个分数时，才有意义算"最大值减最小值"这个分散程度；
    # 只有一个分数时没有"分歧"可言，直接给0.0。
    score_range = (max(valid_scores) - min(valid_scores)) if len(valid_scores) > 1 else 0.0
    high_uncertainty = score_range > _HIGH_UNCERTAINTY_THRESHOLD   # 分歧超过阈值就标记为"高度不确定"
    return {"ok": True, "score": median_score, "score_range": score_range, "high_uncertainty": high_uncertainty, ...}
```

几个值得留意的设计点：

**`max(1, min(int(judge_count), 5))`**——不管调用方传了多离谱的`judge_count`（0、-1、999），实际执行的调用次数都会被安全地限制在1到5次之间。这是一种"参数消毒"（sanitization）：不相信外部传入的参数一定合理，而是主动把它约束到一个安全范围内。之所以封顶5次，是因为每多一次judge调用就多一份真实成本——"评判本身"不应该比"被评判的操作"消耗更多资源。

**中位数而不是平均数**——`statistics.median`对个别极端值不敏感。假设3次评判分别打了0.8、0.85、0.1分（最后一次可能是模型抽风），平均数会被那个0.1明显拉低，但中位数（0.8）会更真实地反映"大多数评判意见其实是一致的"。

**`score_range`：一个曾经被错误命名的指标**。模块docstring里坦诚地记录了一处提炼时发现的措辞问题：

> 原版命名这个分散度指标为"variance"，但实际计算的是`max-min`（极差），不是统计学意义上的方差——本版直接命名为`score_range`，避免用词和实际计算不符造成误导。

这是一个提醒：**统计学术语是有精确含义的，"variance"（方差）和"range"（极差）是两种不同的计算方式**，虽然都能粗略地反映"这组数据有多分散"，但公式完全不同（方差涉及平方和平均，极差只是最大值减最小值）。如果代码里的变量名和它实际的计算方式对不上，接手代码的人很容易被误导，以为自己在用一个统计学意义上的方差做判断，实际上用的是完全不同的另一种东西。**变量命名不只是风格问题，命名和实现不一致本身就是一种潜在的bug源**。

## `_parse_judge_output`：一个"防御性解析"的好例子

大模型即使被明确要求"只输出JSON"，也经常不完全听话，喜欢把JSON包在Markdown代码块里（比如带着```json前缀）。`_parse_judge_output`专门处理了这种情况：

```python
if text.startswith("```"):   # 判断文本开头是不是Markdown代码块围栏
    text = text.strip("`")   # 去掉首尾的反引号字符
    # str.partition("\n")——按第一个换行符把字符串切成三部分：
    # 换行符之前、换行符本身、换行符之后。这里只关心前后两段，
    # 中间那段（换行符本身）用_忽略掉不接收。
    first_line, _, rest = text.partition("\n")
    # 如果第一行非空、且不是以{或[开头（说明它是"json"这类语言标签，
    # 不是真正的JSON内容），就把这一行丢弃，只保留剩下的部分。
    if first_line.strip() and not first_line.strip().startswith(("{", "[")):
        text = rest
```

这里有一个曾经存在的问题（从docstring/注释里的措辞可以看出这是经过打磨的实现）：如果只是简单地用`.strip("`")`去掉首尾的反引号，代码块围栏语言标签（比如"json"这个词）会残留在文本开头，导致后续`json.loads`直接失败。改进后的写法先用`.partition("\n")`把文本切成"第一行"和"剩下部分"，判断第一行是不是一个纯粹的语言标签（不以`{`或`[`开头），如果是就把它丢弃，只保留真正的JSON内容继续解析。

再往下看这一段特别值得记住的检查：

```python
# 专门检查raw_score是不是布尔值——因为Python里bool是int的子类，
# float(True)会得到1.0，不会报错，容易被误当成一个合法的满分。
if isinstance(raw_score, bool):
    return None   # 发现是布尔值，直接判定为无效响应，不继续往下解析
score = float(raw_score)   # 走到这里说明确实是数字，安全地转换成浮点数
```

为什么要先检查`isinstance(raw_score, bool)`？因为Python里`bool`其实是`int`的子类——`float(True)`会得到`1.0`，不会报任何错误。如果judge模型误输出了`{"score": true}`这种格式不对的响应（本该是`{"score": 0.85}`这样的浮点数），不做这层检查的话，`true`会被悄悄当成`1.0`（满分）接受，而这本应该被识别为"格式不符合约定的无效响应"，应该被丢弃、不计入有效分数。**这是一个专门为"防止一个数据类型的巧合子类关系导致语义错误"而写的检查**，体现的是对Python类型系统边界情况的敏感——如果没有专门测试过"judge模型输出了布尔值而不是数字"这种边界情况，这个bug很可能永远不会被发现。

## 本章小结

- 用大模型评判大模型的输出时，不能只信任单次评判结果——独立调用多次、取中位数（而非平均数，避免被极端值拉偏）能提高结果的可信度。
- 除了给出分数，还应该衡量"多次独立评判的分歧程度"，分歧过大时明确标记为"高度不确定"，交给上游决定是否需要人工复核。
- 变量命名必须和实际计算逻辑一致——把"极差"错误地命名为"方差"会误导后续维护者。
- 解析大模型输出时要做防御性处理：既要应对常见的格式偏差（Markdown代码块围栏），也要留意类型系统的边界情况（`bool`是`int`的子类，可能悄悄"蒙混过关"）。

## 动手做

```python
from ainative_prompt.judge import _parse_judge_output

# 场景一：标准的、干净的JSON文本，应该能正确解析。
print(_parse_judge_output('{"score": 0.9, "reasoning": "good"}'))
# 场景二：被Markdown代码块围栏包裹的JSON，应该也能正确剥离围栏后解析。
print(_parse_judge_output('```json\n{"score": 0.9, "reasoning": "good"}\n```'))
print(_parse_judge_output('{"score": true, "reasoning": "??"}'))  # 应该返回None
print(_parse_judge_output('not even json'))  # 应该返回None
```

## 面试可能会问

**问：如果要用LLM来评判另一个LLM的输出质量，你会怎么提高这个评判结果的可信度？**

答题思路：先指出"单次LLM评判本身也有随机性，不能直接当作标准答案"，再讲"独立多次调用+中位数聚合"的方法（并说明为什么用中位数而不是平均数）；进一步补充"应该衡量多次评判之间的分歧程度，分歧大时标记不确定性而不是假装一切正常"。如果能提到"评判次数需要有上限，因为评判本身也是成本"，说明你考虑到了工程上的成本约束，而不只是"理论上越多次越准"这种一厢情愿的想法。
