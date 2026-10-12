"""GUI 导入自检：import gui.app 无报错、无副作用（不建 Tk 实例、不 import torch）.

运行：python3 tests/test_gui_import.py（无显示环境也可跑）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    assert 'torch' not in sys.modules, 'import 前 torch 不应被加载'
    import gui.app as appmod
    assert 'torch' not in sys.modules, 'import gui.app 不应顺带 import torch（延迟加载约束）'
    assert hasattr(appmod, 'PhotoRestoreApp'), '缺少 PhotoRestoreApp 类'
    assert hasattr(appmod, 'main'), '缺少 main 入口'
    # 框架选择论证必须写在模块注释里（验收：注释中论证）
    assert 'PySide' in (appmod.__doc__ or ''), '模块注释缺少 Tkinter/PySide 选型论证'
    print('[1] import gui.app 无报错，且未触发 torch 导入')
    print('[2] PhotoRestoreApp / main 入口存在')
    print('[3] 框架选型论证在模块注释中')
    print('ALL PASS')


if __name__ == '__main__':
    main()
