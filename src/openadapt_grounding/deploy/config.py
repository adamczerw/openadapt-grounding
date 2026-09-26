"""Configuration settings for OmniParser deployment."""

import os
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    raise ImportError(
        "pydantic-settings not installed. Run: uv pip install openadapt-grounding[deploy]"
    )


_env_file_cache: "Path | None | bool" = False  # False = not yet searched


def _find_env_file() -> Path | None:
    """Find .env file by walking up from cwd or package directory."""
    global _env_file_cache
    if _env_file_cache is not False:
        return _env_file_cache  # type: ignore[return-value]

    # Try cwd first
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        env_path = parent / ".env"
        if env_path.exists():
            print(f"[config] Using .env: {env_path}")
            _env_file_cache = env_path
            return env_path
        # Stop at common project boundaries
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            break

    # Fall back to package directory
    pkg_env = Path(__file__).parent.parent.parent.parent / ".env"
    if pkg_env.exists():
        print(f"[config] Using .env (fallback): {pkg_env}")
        _env_file_cache = pkg_env
        return pkg_env

    print("[config] No .env file found")
    _env_file_cache = None
    return None


def _get_project_root() -> Path:
    """Return the project root directory (where .env lives), or cwd as fallback."""
    env = _find_env_file()
    return env.parent if env else Path.cwd()


class DeploySettings(BaseSettings):
    """Configuration settings for AWS deployment."""

    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AWS credentials
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    # Project settings
    PROJECT_NAME: str = "omniparser"
    REPO_URL: str = "https://github.com/microsoft/OmniParser.git"

    # EC2 settings
    # AWS Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04) - G6 compatible
    AWS_EC2_AMI: str = "ami-04631c7d8811d9bae"
    AWS_EC2_DISK_SIZE: int = 128  # GB
    AWS_EC2_INSTANCE_TYPE: str = "g6.xlarge"  # L4 24GB $0.805/hr (faster than g4dn)
    AWS_EC2_USER: str = "ubuntu"

    # Server settings
    PORT: int = 8000  # FastAPI port
    COMMAND_TIMEOUT: int = 600  # 10 minutes

    # Auto-shutdown settings
    INACTIVITY_TIMEOUT_MINUTES: int = 60  # Stop instance after this many minutes of low CPU

    @property
    def CONTAINER_NAME(self) -> str:
        return f"{self.PROJECT_NAME}-container"

    @property
    def AWS_EC2_KEY_NAME(self) -> str:
        return f"{self.PROJECT_NAME}-key"

    @property
    def AWS_EC2_KEY_PATH(self) -> str:
        return str(_get_project_root() / f"{self.AWS_EC2_KEY_NAME}.pem")

    @property
    def AWS_EC2_SECURITY_GROUP(self) -> str:
        return f"{self.PROJECT_NAME}-SecurityGroup"


class UITarsSettings(BaseSettings):
    """Configuration settings for UI-TARS deployment."""

    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AWS credentials (shared)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    # Project settings
    PROJECT_NAME: str = "uitars"
    MODEL_ID: str = "ByteDance-Seed/UI-TARS-1.5-7B"

    # EC2 settings - needs more CPU for vLLM
    AWS_EC2_AMI: str = "ami-04631c7d8811d9bae"  # Same DLAMI as OmniParser
    AWS_EC2_DISK_SIZE: int = 100  # GB (model ~15GB + docker)
    AWS_EC2_INSTANCE_TYPE: str = "g6.2xlarge"  # L4 24GB, 8 vCPU $0.98/hr
    AWS_EC2_USER: str = "ubuntu"

    # Server settings
    PORT: int = 8001  # Different from OmniParser
    COMMAND_TIMEOUT: int = 900  # 15 minutes (model download can be slow)

    # vLLM settings
    MAX_MODEL_LEN: int = 32768
    GPU_MEMORY_UTILIZATION: float = 0.90

    # Auto-shutdown settings
    INACTIVITY_TIMEOUT_MINUTES: int = 60

    @property
    def CONTAINER_NAME(self) -> str:
        return f"{self.PROJECT_NAME}-container"

    @property
    def AWS_EC2_KEY_NAME(self) -> str:
        return f"{self.PROJECT_NAME}-key"

    @property
    def AWS_EC2_KEY_PATH(self) -> str:
        return str(_get_project_root() / f"{self.AWS_EC2_KEY_NAME}.pem")

    @property
    def AWS_EC2_SECURITY_GROUP(self) -> str:
        return f"{self.PROJECT_NAME}-SecurityGroup"


# Global settings instances
settings = DeploySettings()
uitars_settings = UITarsSettings()

# Propagate credentials to os.environ so boto3 can find them
if settings.AWS_ACCESS_KEY_ID:
    os.environ.setdefault("AWS_ACCESS_KEY_ID", settings.AWS_ACCESS_KEY_ID)
if settings.AWS_SECRET_ACCESS_KEY:
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", settings.AWS_SECRET_ACCESS_KEY)
if settings.AWS_REGION:
    os.environ.setdefault("AWS_DEFAULT_REGION", settings.AWS_REGION)
