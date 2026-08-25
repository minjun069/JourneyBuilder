import sys
import os

# tests/ 에서 search/, generator/, spark_jobs/ 를 직접 import 가능하도록
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
