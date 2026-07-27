from .factory import create_app
from .core.observability import configure_logging
from .core.settings import get_settings


settings = get_settings()
configure_logging(
    level=settings.log_level,
    environment=settings.app_env,
    version=settings.app_version,
    process="api",
)
app = create_app(settings)
