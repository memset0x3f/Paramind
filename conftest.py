import sys
import os
import logging

logging.basicConfig(
    filename="paramind.log",
    format="%(asctime)s  %(filename)s : %(levelname)s  %(message)s",
    level=logging.DEBUG,
)

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
