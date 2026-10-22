"""Small discourse rules; they resolve conversation, not lore truth."""
import re


EXPLANATIONS = frozenset((
    "为什么", "为什么呢", "为什么这么觉得", "为什么这样觉得", "为什么这样说",
    "为什么这么说", "为什么喜欢她", "为什么信任她", "怎么说", "怎么说呢",
    "具体说说", "具体说说吧", "继续", "接着说", "然后呢",
))


def bare_text(text):
    return re.sub(r"[\s，,。.!！?？~～]", "", text)


def conversation_act(text):
    """A bounded style hint, never a factuality or retrieval exemption.

    Mixed questions stay general so a greeting cannot hide a lore request.
    This is intentionally a small heuristic, not a semantic intent classifier.
    """
    raw = str(text or '').strip()
    q = bare_text(raw).casefold()
    statement = not re.search(
        r'什么|为什么|怎么|如何|是谁|哪(?:个|位|里)|是不是|是否|能否|介绍|解释|'
        r'想(?:知道|了解|问|听|聊)|谈谈|讲讲|请|帮我|拍照|拍张|拍摄|倒计时|打开|关闭|执行|运行|[?？]', raw)
    if re.fullmatch(r'(?:你好|您好|嗨|哈喽|哈罗|早安|早上好|上午好|下午好|晚上好|晚安|早|hello|hi|hey)(?:呀|啊|哇|哦)?(?:friston(?:-?3)?|弗里斯顿)?', q):
        return 'greeting'
    if (re.fullmatch(r'(?:谢谢(?:你|您|啦|了)?|多谢(?:你|您)?|感谢你|辛苦了|thankyou|thanks)(?:啊|呀|哦|呢)?', q)
            or statement and len(q) <= 50 and re.match(r'^(?:谢谢|多谢|感谢你|辛苦了)', q)):
        return 'thanks'
    if re.fullmatch(r'(?:嗯|嗯嗯|好|好的|哦|噢|啊|原来如此)*(?:我)?(?:明白了|知道了|了解了|懂了)(?:呀|啊|呢|谢谢)?', q):
        return 'acknowledgement'
    # Match a complete personal report, not an emotion word anywhere in a
    # short sentence. A named character's feelings remain a lore question.
    time = r'今天|最近|现在|刚刚|这几天|这会儿|此刻'
    personal = r'(?:(?:我(?:' + time + r')?|(?:' + time + r')(?:我)?))?'
    degree = r'(?:(?:真的|实在|感觉|觉得|有点|有些|有一点|有点儿|好|很|挺|太|特别|非常))*'
    feeling = r'(?:累|困|疲惫|疲倦|难过|孤独|害怕|失落|伤心|焦虑|烦躁|不开心|开心|高兴|不好|糟糕|很差)'
    if (statement and len(q) <= 65
            and re.match(r'^(?:我|' + time + r')', q)
            and re.search(degree + feeling + r'(?:了|呢|啊|呀|唉|嘛)*$', q)):
        return 'emotion'
    support = (r'(?:(?:只)?想(?:休息|歇|睡)(?:一下|一会儿?|会儿?)?(?:了)?|'
               r'(?:你)?(?:能|可以)?(?:陪我|和我)(?:聊聊|聊一会儿?|说说话|说两句)(?:吗|吧)?)?')
    if len(q) <= 65 and re.fullmatch(
            personal + r'(?:心情)?' + degree + feeling + r'(?:了|的|呢|啊|呀|唉|嘛)*' + support, q):
        return 'emotion'
    if len(q) <= 65 and re.fullmatch(
            personal + r'(?:逛(?:了)?(?:漫)?展|排(?:了)?队|走(?:了)?(?:好多路|一天|好久)?|站(?:了)?(?:一天|好久)?)'
            + r'(?:走|逛|排|站)?(?:得)?' + degree + feeling
            + r'(?:了|的|呢|啊|呀|唉|嘛)*' + support, q):
        return 'emotion'
    # Keep explicit information requests out of social-only routing.
    if re.search(r'什么|为什么|怎么|如何|是谁|哪(?:个|位|里)|介绍|解释|知道|认识|[?？]', raw):
        return 'general'
    if (len(q) <= 45 and re.search(r'你|这(?:个|副|台)|设计|造型|回答', q)
            and re.search(r'不错|很好|很棒|真棒|真厉害|真好|做得好|很厉害|漂亮|喜欢你|夸你|很有帮助|有意思|有趣', q)
            and not re.search(r'不(?:错才怪|太|怎么|喜欢)|一点也不|并不|谈不上', q)):
        return 'compliment'
    if (statement and len(q) <= 100 and re.match(
            r'^(?:(?:我|我们|咱们)(?:今天|刚才|最近|现在)|(?:今天|刚才|最近|现在)(?:我|我们|咱们))', q)):
        return 'personal_report'
    return 'general'


def reference_description(text, subjects):
    """An unnamed nickname/appearance is not the last character's identity."""
    if any(s.get("kind") in ("character", "machine", "fan_machine") for s in subjects):
        # The speaker's own node does not resolve an unnamed other person.
        from friston3.npc.identity import IDENTITY
        if any(s.get('id') != IDENTITY.entity_id for s in subjects):
            return ""
    if '兜帽男' in text:
        return '兜帽男'
    match = re.search(r"(?:有(?:一)?(?:只|个|位)|那(?:只|个|位))([^，。！？?]{1,14})", text)
    if not match:
        return ""
    description = re.sub(r"(?:是不是|是谁|叫什么|吗|吧|呢|呀|啊)+$", "", match.group(1))
    if any(word in description for word in ("兔", "猫", "狐狸", "耳朵", "尾巴", "头发", "家伙", "姑娘")):
        return description
    return ""


def discourse_act(text, subjects, previous_question, pending_reference=None):
    """Only a short explicit name can settle a pending referent/correction."""
    q = bare_text(text)
    if q in EXPLANATIONS:
        return "explanation"
    names = [s["name"] for s in subjects if s.get("kind") in ("character", "machine")]
    if previous_question and names:
        name_pattern = "(?:" + "|".join(re.escape(name) for name in names) + ")"
        if re.fullmatch(r"(?:不对|不是|应该是|是|我说的是|我指的是)?" + name_pattern + r"(?:吧|才对|啊|呀|对吧|不是吗)?", q):
            if pending_reference or q.endswith(("吧", "才对", "对吧", "不是吗")) or q.startswith(("不对", "应该是", "我指的是", "我说的是")):
                return "correction"
    return "question"
