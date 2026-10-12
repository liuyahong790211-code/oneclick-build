"""安装包启动器：pythonw 无控制台启动 GUI（小白省心）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 权重默认位置：安装包内置 models/pytorch_model.pt（环境变量可覆盖）
os.environ.setdefault('PHOTORESTORE_MODEL', os.path.join(ROOT, 'models', 'pytorch_model.pt'))

from gui.app import main

if __name__ == '__main__':
    main()
