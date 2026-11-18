# ============================================================
# crawler-agent 运行镜像
#
# 单阶段（base + runtime 合一），刻意不做 day01 那个三阶段：
#   day01 有 test 阶段是因为「容器内 pytest 全绿」是它的验收项；
#   crawler-agent 的测试全在宿主机跑（要连本机 MySQL、要真模型 key），
#   容器里再装一遍 pytest 只会白占体积。等 CI 上线再加 test 阶段。
#
# 两条不可省的约定：
#   1. 先 COPY requirements.txt 再 pip install —— 只要依赖没变，改代码不会重装依赖
#   2. --host 0.0.0.0 —— 默认 127.0.0.1 只在容器内部监听，宿主机映射进不来
# ============================================================
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE：容器里没人复用 __pycache__，别写
# PYTHONUNBUFFERED      ：日志立刻刷出来，否则 docker logs 看不到实时输出
# PIP_NO_CACHE_DIR      ：pip 缓存不留在层里
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# pip 源默认走清华镜像：国内直连 pypi.org 经常几十 KB/s 甚至超时，
# 首次 build 会卡在装依赖那一步。要「别人 clone 就能构建」时用官方源覆盖：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple -t crawler-agent:v0.1 .
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# ★ 顺序即缓存策略：requirements.txt 没变 → 这一层走缓存
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt --index-url "$PIP_INDEX_URL"

# 非 root 运行：应用被攻破时攻击者拿到的是普通用户，不是 root
RUN useradd --create-home --uid 1000 appuser

# 复制代码。哪些文件不该进来由 .dockerignore 决定 ——
# 尤其是 .env（里面有 API key 和 JWT 密钥），绝不能烤进镜像层：
# 镜像层是「只删不真删」的，删了也还在历史里。
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# 注意没有 --reload：容器里 reload 依赖文件监听，纯浪费 CPU；
# 改代码要生效就重新 build（或用 compose 的 volumes 挂代码，那是另一种用法）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
