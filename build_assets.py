# -*- coding: utf-8 -*-
"""把 web/index.html 重新内联进 web_assets.py。

改完界面后运行一次即可：
    python build_assets.py

为什么要内联：
  - GitHub 网页上传不支持子文件夹，扁平结构才能整包拖上去
  - PyInstaller 不再需要 --add-data，少一个出错环节
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "web", "index.html")
DST = os.path.join(HERE, "web_assets.py")


def main():
    if not os.path.exists(SRC):
        print("找不到源文件:", SRC)
        return 1
    html = io.open(SRC, encoding="utf-8").read()
    with io.open(DST, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write('"""内置网页界面（由 build_assets.py 从 web/index.html 自动生成，请勿手改）。\n\n'
                "把界面内联进代码，是为了让上传/打包不再依赖 web 子目录：\n"
                "  - GitHub 网页上传不支持子文件夹，扁平结构才能拖上去\n"
                "  - PyInstaller 无需 --add-data，少一个出错环节\n"
                "改界面请改 web/index.html，然后运行：python build_assets.py\n"
                '"""\n\n')
        f.write("INDEX_HTML = " + repr(html) + "\n")
    print("已生成 %s（%d 字节，源自 %s）" % (DST, os.path.getsize(DST), SRC))
    return 0


if __name__ == "__main__":
    sys.exit(main())
