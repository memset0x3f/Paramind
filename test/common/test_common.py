from common import RuntimeException, Config
import os


def testRuntimeException():
    try:
        try:
            raise RuntimeException("Test error", "start")
        except RuntimeException as e:
            e.appendExecPath("end")
            raise e
    except RuntimeException as e:
        assert str(e) == "start->end: Test error"
    except Exception as e:
        assert False, f"Unexpected exception type: {type(e)}"


def testConfigLoadSave():
    config_data = {
        "section1": {"key1": "value1", "key2": 42},
        "section2": {"keyA": True, "keyB": 3.14},
    }

    config_file = "test_config.toml"

    # Save configuration to file
    config = Config()
    config.update(config_data)
    config.saveToFile(str(config_file))

    # Load configuration from file
    loaded_config = Config(str(config_file))

    assert loaded_config == config_data

    # Clean up
    os.remove(config_file)
