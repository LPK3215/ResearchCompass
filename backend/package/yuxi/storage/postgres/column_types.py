"""PostgreSQL column types shared by the ORM models."""

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator
from yuxi.utils.datetime_utils import UTC


class UTCDateTime(TypeDecorator):
    """TIMESTAMPTZ column that treats naive bind values as UTC.

    业务代码统一用 utc_now_naive() 生成 naive UTC 时间；asyncpg 在写入
    timestamptz 时会把 naive datetime 当成本地时区（Asia/Shanghai），导致
    落库时刻提前 8 小时。该类型在绑定参数时统一标注 UTC，无需修改调用点。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
