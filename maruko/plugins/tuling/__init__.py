import re
import asyncio
import math
import json
import hashlib
from typing import List, Optional, Union, Dict, Collection, Any

import aiohttp
from aiocqhttp.message import Message, escape
from none import on_command, CommandSession
from none import on_natural_language, NLPSession, NLPResult
from none import get_bot
from none.helpers import context_id

from maruko import baidu_aip
from maruko.log import logger

bot = get_bot()

# key: context id, value: named entity type
tuling_sessions = {}


def tuling_ne_type(replies: List[str],
                   keywords: Dict[str, Collection[Any]]) -> Optional[str]:
    for reply in replies:
        for ne_type, ne_keywords in keywords.items():
            for kw in ne_keywords:
                if isinstance(kw, type(re.compile(''))):
                    match = bool(kw.search(reply))
                else:
                    match = kw in reply
                if match:
                    return ne_type


@on_command('tuling', aliases=('聊天', '对话'))
async def tuling(session: CommandSession):
    message = session.get('message', prompt='我已经准备好啦，来跟我聊天吧~')

    ctx_id = context_id(session.ctx)
    if ctx_id in tuling_sessions:
        del tuling_sessions[ctx_id]

    tmp_msg = Message(message)
    text = tmp_msg.extract_plain_text()
    images = [s.data['url'] for s in tmp_msg
              if s.type == 'image' and 'url' in s.data]

    # call tuling api
    replies = await call_tuling_api(context_id(session.ctx),
                                    text, images)
    logger.debug(f'Got tuling\'s replies: {replies}')

    if replies:
        for reply in replies:
            await session.send(escape(reply))
            await asyncio.sleep(0.8)
    else:
        await session.send('看不懂你在说什么呢')

    one_time = session.get_optional('one_time', False)
    if one_time:
        # tuling123 may opened a session, we should recognize the
        # situation that tuling123 want more information from the user.
        # for simplification, we only recognize named entities,
        # and since we will also check the user's input later,
        # here we can allow some ambiguity.
        loc_keywords = ('哪里', '哪儿', re.compile(r'哪\S城市'), '位置')
        time_keywords = ('什么时候',)
        ne_type = tuling_ne_type(replies, {
            'LOC': loc_keywords,
            'TIME': time_keywords,
        })
        if ne_type:
            logger.debug(f'One time call, '
                         f'and there is a tuling session for {ne_type}')
            tuling_sessions[ctx_id] = ne_type
    else:
        session.pause()


@tuling.args_parser
async def _(session: CommandSession):
    if session.current_key == 'message':
        # TODO: 表达再见的意思时 finish
        if session.current_arg_text.strip() in ('结束', '拜拜', '再见'):
            session.finish('拜拜啦，你忙吧，下次想聊天随时找我哦~')
            return
        session.args[session.current_key] = session.current_arg


@on_natural_language(only_to_me=False)
async def _(session: NLPSession):
    confidence = None

    if session.ctx['to_me']:
        confidence = 60.0

    ctx_id = context_id(session.ctx)
    if ctx_id in tuling_sessions:
        ne_type = tuling_sessions[ctx_id]
        words = await baidu_aip.lexer(session.msg_text)
        for w in words:
            if ne_type == w['ne']:
                confidence = 100.0 - len(words) * 5.0
                break

    if confidence:
        return NLPResult(confidence, 'tuling', {
            'message': session.msg,
            'one_time': True
        })


@on_natural_language(keywords={'聊', '说话'})
async def _(session: NLPSession):
    text = session.msg_text.strip()
    confidence = 0.0
    match = len(text) <= 4 and '陪聊' in text
    if match:
        confidence = 100.0
    else:
        score = await baidu_aip.simnet('来陪我聊天', text)
        if score > 0.70:
            match = True
            confidence = math.ceil(score * 10) * 10  # 0.74 -> 80.0

    if match:
        return NLPResult(confidence, 'tuling', {})


async def call_tuling_api(
        ctx_id: str,
        text: Optional[str],
        image: Optional[Union[List[str], str]]) -> List[str]:
    url = 'http://openapi.tuling123.com/openapi/api/v2'
    payload = {
        'reqType': 0,
        'perception': {},
        'userInfo': {
            'apiKey': bot.config.TULING123_API_KEY,
            'userId': hashlib.md5(ctx_id.encode('ascii')).hexdigest()
        }
    }

    if image and not isinstance(image, str):
        image = image[0]

    if text:
        payload['perception']['inputText'] = {'text': text}
        payload['reqType'] = 0
    elif image:
        payload['perception']['inputImage'] = {'url': image}
        payload['reqType'] = 1
    else:
        return []

    try:
        async with aiohttp.request('POST', url, json=payload) as resp:
            if 200 <= resp.status < 300:
                resp_payload = json.loads(await resp.text())
                if resp_payload.get('results'):
                    return_list = []
                    for result in resp_payload['results']:
                        res_type = result.get('resultType')
                        if res_type in ('text', 'url'):
                            return_list.append(result['values'][res_type])
                    return return_list
            return []
    except aiohttp.ClientError:
        return []
