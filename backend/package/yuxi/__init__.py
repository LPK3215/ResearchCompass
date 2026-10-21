from pathlib import Path

from dotenv import load_dotenv

# 本地直跑（uvicorn / arq）时工作目录不固定，按仓库路径固定加载两份配置：
# backend/.env 提供数据库、对象存储等连接信息，仓库根 .env 提供模型密钥与超时等参数，
# 与 Docker Compose 通过 env_file 注入的键保持一致。override=False 保证真实环境变量优先。
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent

load_dotenv(_BACKEND_DIR / ".env", override=False)
load_dotenv(_REPO_ROOT / ".env", override=False)

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from yuxi.config import config as config  # noqa: E402

try:
    from importlib.metadata import version

    __version__ = version("yuxi")
except Exception:
    __version__ = "unknown"

executor = ThreadPoolExecutor()  # noqa: E402


def get_version():
    """Return the Yuxi version."""
    return __version__
