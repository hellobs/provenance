"""断言已安装的 mavisframework 具备 case01 需要的通用插件面。

CI 在装完框架后先跑本脚本:若容器错装到不含插件面的旧制品(<=1.1.0),
这里在跑测试之前就红,而不是让特性探测静默回退、测试"照样通过"。
逐项 try/except 是为了让失败时打印的是"缺了哪一项",而不是一句 import 堆栈。
"""
import inspect
import sys


def main() -> int:
    problems = []

    try:
        import mavisframework
    except Exception as exc:  # noqa: BLE001
        print(f"mavisframework 无法导入: {exc.__class__.__name__}: {exc}")
        return 1

    plugin_mod = None
    try:
        import mavisframework.plugin as plugin_mod
    except Exception as exc:  # noqa: BLE001
        problems.append(f"mavisframework.plugin 无法导入: {exc.__class__.__name__}: {exc}")

    agent_core = None
    try:
        from mavisframework.core import agent_core
    except Exception as exc:  # noqa: BLE001
        problems.append(f"mavisframework.core.agent_core 无法导入: {exc.__class__.__name__}: {exc}")

    simulator_cls = None
    try:
        from mavisframework.runtime.simulator import Simulator as simulator_cls
    except Exception as exc:  # noqa: BLE001
        problems.append(f"mavisframework.runtime.simulator.Simulator 无法导入: {exc.__class__.__name__}: {exc}")

    if plugin_mod is not None:
        if not hasattr(plugin_mod, "Plugin"):
            problems.append("mavisframework.plugin.Plugin 缺失")
        if not hasattr(plugin_mod, "PluginManager"):
            problems.append("mavisframework.plugin.PluginManager 缺失")
    if agent_core is not None and not hasattr(agent_core, "subscribe_chat_line"):
        problems.append("mavisframework.core.agent_core.subscribe_chat_line 缺失")
    if simulator_cls is not None:
        params = inspect.signature(simulator_cls.__init__).parameters
        if "plugins" not in params:
            problems.append("Simulator.__init__ 无 plugins 参数")
    else:
        params = None

    print(f"mavisframework {mavisframework.__version__} @ {mavisframework.__file__}")
    # 元数据版本也要露出来(2026-09-24 体检):editable 装的 dist-info 不会随代码升版本,
    # 实测本机是「代码 1.3.0 / pip 元数据 1.2.1」—— 只看 __version__ 会把这件事藏起来,
    # 而"装的到底是哪一版"在排查依赖与对接问题时天天要用。
    try:
        import importlib.metadata as _md
        installed = _md.version("mavisframework")
    except Exception as exc:  # noqa: BLE001
        installed = "(读不到: {})".format(exc.__class__.__name__)
    if installed != mavisframework.__version__:
        print("  [!] 已安装元数据版本 = {},与代码版本不一致 —— editable 安装的元数据"
              "不会自动跟随;要消除请重装(在 mavis 仓:pip install -e .)".format(installed))
    else:
        print("  (已安装元数据版本 = {})".format(installed))
    if params is not None:
        print("Simulator.__init__ 参数: " + ", ".join(params))

    if problems:
        print("插件面基线断言失败:")
        for item in problems:
            print("  - " + item)
        print("框架必须从 hellobs/mavis main(>=1.2.0)以源码安装;旧制品不含插件面。")
        return 1
    print("插件面基线断言通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
