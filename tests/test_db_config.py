"""数据库连接配置回归测试。"""

from sqlalchemy.engine import URL

from app.config import Settings


def test_db_url_preserves_special_characters_in_password():
    """强密码中的 URL 特殊字符不应破坏连接参数。"""
    password = "strong@pass:/?#[]"
    settings = Settings(
        _env_file=None,
        DB_HOST="database.internal",
        DB_PORT=3306,
        DB_NAME="data",
        DB_USER="root",
        DB_PASSWORD=password,
    )

    assert isinstance(settings.dbUrl, URL)
    assert settings.dbUrl.password == password
    assert settings.dbUrl.host == "database.internal"
    assert settings.dbUrl.database == "data"


def test_database_timeout_defaults_are_bounded():
    """默认超时应快速释放异常数据库连接。"""
    settings = Settings(_env_file=None)

    assert settings.dbConnectTimeoutSec == 3
    assert settings.dbPoolTimeoutSec == 5
    assert settings.dbReadTimeoutSec == 10
    assert settings.dbWriteTimeoutSec == 10
