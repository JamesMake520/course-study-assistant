# Windows 发行包的第三方许可

本目录按组件保存上游许可、版权声明和致谢。项目根目录的 MIT 许可证适用于本项目代码。第三方组件按各自原文使用或再分发。

| 组件和发行版本 | 在程序中的用途 | 许可文件或目录 |
|---|---|---|
| CPython 3.14.6 | Python 解释器、标准库及扩展 | [PSF 及历史许可](CPython/LICENSE)、[此版本标准库第三方致谢](CPython/third-party-notices.rst) |
| Playwright for Python 1.64.1 | 浏览器控制接口，© Microsoft Corporation | [Apache 2.0](Playwright-Python/LICENSE) |
| playwright-core 1.64.0-beta-1791568591000 | 浏览器驱动，含 Puppeteer 衍生代码、内嵌 JavaScript 库以及 WebP 编解码器 | [许可证、NOTICE 和逐文件第三方声明](Playwright-Core/) |
| Node.js v24.22.1 | 启动 Playwright 驱动 | [Node.js 及其内嵌依赖的许可和致谢](Node.js/LICENSE) |
| greenlet 3.5.6 | 在同步接口和异步浏览器驱动间切换 | [MIT 与 Stackless Python 的 PSF 许可](greenlet/) |
| pyee 14.0.0 | 传递浏览器事件 | [MIT](pyee/LICENSE) |
| PyInstaller 6.22.3 | 可执行文件的启动器及运行时钩子 | [GPL、启动器例外及运行时钩子条款](PyInstaller/COPYING.txt) |
| OpenSSL 3.6.4 | TLS 与加密 | [Apache 2.0](OpenSSL/LICENSE.txt)、[作者及权利人](OpenSSL/AUTHORS.md) |
| libffi 3.4.8 | Python 的 C 函数调用 | [MIT](libffi/LICENSE) |
| Expat 2.8.1 | XML 解析 | [MIT](Expat/COPYING) |
| mpdecimal 4.0.1 | 十进制定点运算 | [版权及 BSD 许可](mpdecimal/COPYRIGHT.txt) |
| SQLite 3.53.2 | 本地数据库 | [公共领域声明](SQLite/LICENSE.md) |
| bzip2 1.0.8、zlib 1.3.2、XZ Utils 的 liblzma 5.8.3、Zstandard 1.5.7 | 数据压缩及解压 | [bzip2](bzip2/LICENSE)、[zlib](zlib/LICENSE)、[XZ Utils](XZ-Utils/)、[Zstandard](Zstandard/LICENSE) |

OpenSSL 版权所有 © 1998–2026 The OpenSSL Project Authors，© 1995–1998 Eric A. Young、Tim J. Hudson。[上游版权声明](https://github.com/openssl/openssl/blob/openssl-3.6.4/README.md)

Playwright 的上游界面资源涉及 React、React DOM、Scheduler、CodeMirror、xterm.js、FitAddon、YAML、zip.js 和 jsdiff。相应软件包的许可证按名称分列。Codicons 图形和字体由 Microsoft 及贡献者提供，适用 CC BY 4.0，关联代码采用 MIT。字体源于上游 Playwright 发行文件，本项目未修改字形。浏览器名称和标识属于各自权利人。[界面组件许可](Playwright-UI/) · [Codicons 许可](Codicons/) · [Codicons 原项目](https://github.com/microsoft/vscode-codicons) · [Playwright 该版本源码](https://github.com/microsoft/playwright/tree/1b2924712024804936dbcfe9fabaa655d52325e5)

构建环境另安装了 typing_extensions 4.16.0，附其 [PSF 许可](typing_extensions/LICENSE)。本次冻结模块清单未列入该模块。

Windows 的 UCRT 与 Microsoft Visual C++ x64 Redistributable 由系统单独提供，发行压缩包排除了这两组运行库的 DLL。安装方式见程序使用说明。[Microsoft 官方运行库说明](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
