"""配置模块单元测试"""

import pytest
from modules.config import Config, load_config, ServerConfig, DataConfig, ScoringConfig


class TestConfig:
    def test_default_values(self):
        config = Config()
        assert config.server.port == 5559
        assert config.data.timeout == 10
        assert config.scoring.min_market_cap == 30.0

    def test_load_config(self):
        config = load_config()
        assert isinstance(config, Config)
        assert isinstance(config.server, ServerConfig)
        assert isinstance(config.data, DataConfig)
        assert isinstance(config.scoring, ScoringConfig)

    def test_frozen(self):
        config = Config()
        with pytest.raises(AttributeError):
            config.server.port = 9999
