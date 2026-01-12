from typing import Optional
from common.Concepts import RuntimeException
import toml


class Config(dict):
    def __init__(self, configFile_: Optional[str] = None):
        self.configFile = configFile_
        if configFile_:
            self.loadFromFile(configFile_)

    def loadFromFile(self, configFile_: str):
        self.configFile = configFile_
        with open(configFile_, "r") as f:
            self.clear()
            self.update(toml.load(f))

    def saveToFile(self, configFile_: Optional[str] = None):
        if configFile_ is not None:
            self.configFile = configFile_
        if not self.configFile:
            raise RuntimeException(
                "No configuration file specified.", "Config.saveToFile"
            )

        with open(self.configFile, "w") as f:
            toml.dump(self, f)
