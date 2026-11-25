"""接口鉴权的公共依赖。

方向提醒：这里的 settings.app_api_key 是「别人进来调我」的门票，
和 config 里 deepseek_api_key 那种「我出去调别人」的厂商凭据不是一回事。
两者永远不要混：拿厂商 key 当门户密码，一旦泄露就连你的 LLM 额度一起送出去。
"""
import secrets

from fastapi import Header, HTTPException

from config import settings


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """校验请求头 X-API-Key，不过就 401。

    两个分支：
      ① 服务端没配 app_api_key → 放行。
         启动期 validate_settings 已经拦过（≥16 字符），这里只兜「运行期热改配置」的边角。
      ② 配了 → 请求头必须与它完全一致，否则 401。
    """
    if not settings.app_api_key.strip():
        return

    # not x_api_key 必须写在前面：secrets.compare_digest(None, str) 会抛 TypeError，
    # 结果是「不带头的请求返回 500」而不是 401 —— 500 会让人去查服务器日志，
    # 实际问题在调用方没带凭证。
    #
    # 用 compare_digest 而不是 !=：!= 在第一个不同字符处就短路返回，
    # 响应时间差理论上能让人逐字符猜出密钥（时序攻击）；
    # compare_digest 始终比较完所有字符，耗时不随「第几位不同」变化。
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.app_api_key):
        raise HTTPException(
            status_code=401,
            detail="缺少或错误的 X-API-Key 请求头",
        )
