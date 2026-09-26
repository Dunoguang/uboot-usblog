> **2026-09-26 update** - see [`FINDINGS-2026-09-26.md`](FINDINGS-2026-09-26.md).
> Summary: the USB log is now verified working on hardware, but only with an
> image that forces the 8 KB gserial channel allocation
> (`images/uboot-v26-forceport.img`).  The v1..v25 images could never
> produce data, because u-boot skips the channel allocation on the
> "port open timeout" path (`usb calibrate port open timeout`).
> `[0x30]` must stay 0x75EF0 (it is the sechdr offset base read by vboot).
> The `uboot_log` partition gives a complete offline log of every boot that
> reaches the kernel jump - see `tools/dump_uboot_log.sh`.

# uboot-usblog — DW99 / vp19（展锐 SL8541E）uboot USB 日志抓取

给展锐 uboot 打补丁，把启动日志从 USB 口实时输出，主机用 libusb 抓取。
**免拆机、免串口**，用于 DW99 / vp19 手表的启动调试（例：4.4 → 4.4.302 内核线「不开机、无日志」排查）。

- 设备侧链路：`puts()` hook → `usb_log_puts()` → gserial TX 环形缓冲 → bulk EP5-IN（`1782:4d00`）
- 主机侧工具：`tools/uboot_usb_log.py`（libusb-1.0 via ctypes，无第三方依赖）
- 逆向时间：2026-08 ~ 2026-09；配套反汇编：`analysis/uboot.asm`

> 所有补丁地址均为**镜像文件偏移**；运行时 VA = 文件偏移 + 0x9EFFFE00（file 0x200 → 0x9F000000）。

## 1. 背景

- 启动链：SPL → SML(BL31) → TOS(Trusty) → uboot → kernel。
  已逐一验证 SPL / SML / TOS 均无日志输出逻辑，唯一可用输出面在 uboot。
- USB 校准口：`1782:4d00` “Gadget Serial”（厂商类 0xff，EP5-IN=0x85 / EP6-OUT=0x06，bcdDevice 24.16）。
  该 USB 仅存在于 uboot 阶段，跳内核即消失——所以采集脚本必须**先启动、后上电**，中途断开属正常。
- 取日志的三种途径：
  1. **USB 实时输出**（本仓库，实时、可反复抓）；
  2. 读 uboot_log 分区（开机后从系统内读历史 slot，见 `tools/parse_uboot_log.py`）；
  3. UART（需拆机接线；对照样本见 `reference/`）。

## 2. 补丁原理（v4 最终版）

对原厂 uboot（`images/uboot.img`，484260B，md5 `a03efc…`）的全部修改：

| 区域 | 修改 | 说明 |
|---|---|---|
| 0x30 | DHTB 长度字段 → 0x00076024 | 头部字段 |
| 0xE798 | `puts()` → `B 0x76408` | 打印入口改跳 puts_hook |
| 0x17B0C | → `MOV W0,#1; RET` | 砍 pctool/NV 分支（直接返回成功） |
| 0x17FDC | → `MOV W0,#1; RET` | 同上 |
| 0x1AAF0 | 校准循环体 → `BL 0x763A8` | 进入 stub：SMC USB init + 置 flag |
| 0x1AAF4 | 校准循环体 → `B 0x1AAE8` | 跳出校准循环、继续正常启动 |
| 0x1A318 | AON bit3 校准分支 → `B 0x1AAE8` | 跳过另一条校准路径 |
| 0x763A4~0x76423 | 文件尾追加区（128 B） | flag@0x763A4 / stub@0x763A8 / usb_log_puts@0x763C4 / puts_hook@0x76408 |

追加区三个函数：

- **stub @0x763A8**：`BL 0x1B328`（SMC USB init）→ 置 flag=1 → `BL 0x30EA0`（USB poll）→ RET
- **usb_log_puts @0x763C4**：建帧 → 内联 strlen → `BL 0x2D1D0`（gserial 写 + kick）→ `BL 0x30EA0`（poll）→ 返回
- **puts_hook @0x76408**：复刻原 puts 序言 → 调 usb_log_puts → 跳回原 puts（UART 照常输出，双通道）

v4 与基线全量 diff = **7 个区域**，其余字节逐字节一致（2026-09-13 审查）：
`0x30`、`0xE798`、`0x17B0C~0x17B13`、`0x17FDC~0x17FE3`、`0x1A318`、`0x1AAF0~0x1AAF7`、`0x763A4~0x76423`。
（0x1AA6C / 0x1AA80 两处 CBZ 在 v1 被改动过，v3 已恢复原样，故最终版无差异。）

v3 → v4 的唯一改动：**补 USB 事件 poll**（两处 `BL 0x30EA0`），修复 in-flight 传输泄漏导致的日志卡死。
依据：原 gserial 发送函数 sub_1A8BC = `BL 0x2D1D0`（写入 8KB 环形缓冲 + kick）+ `BL 0x2D2F0`（事件循环）；
`0x30EA0` 是单次非阻塞的 poll（未就绪返回 -22）。v3 漏了 poll → in-flight 计数泄漏 → 日志中途卡死。

安全依据（为什么砍校准不影响启动）：校准只是教室工具/产测用的 USB 收发；本机 uboot 无校验链
（SPL 只查镜像大小、TOS 不校验 uboot），且 v1 修改版**曾实机运行成功**。

## 3. 目录结构

```
uboot-usblog/
├── tools/     采集与解析工具（uboot_usb_log.py 为主）
├── patch/     补丁脚本（v1 之后的全部生成脚本）
├── images/    各版本 uboot 镜像归档（原厂 + v1 + 实验 + v3 + v4）
├── analysis/  IDA 导出的原版反汇编（uboot.asm，Input MD5 = a03efc…）
└── reference/ 对照样本日志（另一台 SL8541E 设备）
```

## 4. 版本链与文件

| 版本 | 镜像 | 大小 | md5 | 生成者 | 说明 |
|---|---|---|---|---|---|
| 基线 | `images/uboot.img` | 484260 | `a03efc263613a61680e892261df90954` | — | 原厂（与 `uboot-unlock-bootloader.img` 同一文件） |
| v1 | `images/uboot-usblog.img` | 484388 | `dbdfbd641bdc453d3d6bc4edb88b6322` | 早期脚本（未归档） | 最早可用版（puts hook + 尾部追加区），曾实机运行 |
| 实验 | `images/uboot-patched.img` | 484260 | `b7f87c0d67a6540705abe17718d38417` | `patch/patch_usb_log.py` | 早期路线①：砍 sub_17FDC / sub_17B0C / sub_2C98 |
| 实验 | `images/uboot-usb-log.img` | 484260 | `743269123a3bdd991073d900b7867908` | `patch/patch_usb_log_output.py` | 早期路线②：UART putc 重定向到 0x17FE4 的 usb_log_putc |
| v3 | `images/uboot-nocal-usblog.img` | 484388 | `513b2ee7254f3fbad926050ab059ee38` | `patch/patch_nocal_usblog.py` | v1 基线 + 砍校准 / 砍 pctool NV + SMC stub |
| **v4** | `images/uboot-nocal-usblog-v4.img` | 484388 | `62897565b0aaf0c6a4937e6e735e0478` | `patch/patch_v4_poll.py` | **最终版**：+ USB poll 修复（已全量审查 + 已实机刷入） |

v1 相对基线的差异：`0x30`、`0xE798`（hook）、`0x1AA6C` / `0x1AA80`（CBZ，后被 v3 恢复）、尾部追加区（`0x763A4~0x76423`）。
v1 → v3 差异：`0x17B0C`、`0x17FDC`（→ MOV;RET）、`0x1A318`、`0x1AAF0~0x1AAF7`、`0x1AA6C` / `0x1AA80`（恢复）、`0x763A8~0x763AB`、`0x763BC~0x763C3`（stub 更新）。

复现（脚本内 SRC/DST 是当时的绝对路径，换机器需先改）：

```sh
python3 patch/patch_nocal_usblog.py   # uboot-usblog.img → uboot-nocal-usblog.img（末尾自校验 ALL PASS）
python3 patch/patch_v4_poll.py        # → uboot-nocal-usblog-v4.img（末尾自校验 ALL PASS）
```

## 5. 使用（采集日志）

依赖：Linux 主机 + libusb-1.0（`apt install libusb-1.0-0`）+ python3（无需 pip 安装任何东西）。

```sh
# 1) 主机先启动采集（此时手表不要上电）
python3 tools/uboot_usb_log.py            # 默认输出 uboot_usb_MMDD_HHMMSS.log（带主机时间戳）
#    可选：-o 指定文件 | --raw 另存原始字节流 | --no-ts 不加时间戳 | --once 抓一次退出 | -q 安静

# 2) 手表插线上电 → 抓 uboot 全阶段 → 跳内核时 USB 消失（正常），脚本自动等待下一次枚举
```

（如遇 `libusb_init failed (permission?)`：用 root 运行或配置 udev 规则。）

其他工具：

- `tools/uboot_usb_log_reader.py` —— 旧版采集器（功能等价，留作参考）
- `tools/parse_uboot_log.py` —— 解析 uboot_log 分区 dump（magic 0xABCD、512B 头、6 slot × 256KB），导出 `slotN_@off.log`

## 6. 实测结论与关键地址

- 单次采集约 **3.7KB（90~92 行）**：是「上电 → 跳内核前 flush」的实际输出量，**不是缓冲上限**。
  （曾误判为 4KB 上限；实际 DTB reserved-memory `logbuffer@92400000 size=0x50000` = 320KB）
- RPMB key 未写：`sprd_get_imgversion` 重试 13 次，占日志约 48%，属正常噪音。
- 日志截止点 = 跳内核 `sub_17524` 的 flush 序列（sub_D1A4 → sub_D1B8 → sub_D434 → MMU → `BLR 0x80080000`），此后无 printf。
- 部分消息字符串搜不到 ADRP+ADD 引用属正常：日志宏按编译期等级裁剪后调用点消失、字符串残留（死字符串）；少数经指针表访问（搜 8 字节 VA 才找得到）。
- uboot 内 `sub_1A69C` 可看到 `loglevel=7`；`fdt_fixup_loglevel` 负责给内核 DTB 打补丁。

关键地址（文件偏移）：

| 符号 | 偏移 | 备注 |
|---|---|---|
| printf | 0xE7D8 | |
| puts | 0xE798 | 本次 hook 点 |
| UART putc | 0x2B2D8 | 输出单个字符 |
| gserial 发送 | sub_1A8BC | = 0x2D1D0 写环形缓冲 + kick；0x2D2F0 事件循环 |
| USB poll | 0x30EA0 | 单次非阻塞，未就绪返回 -22 |
| SMC USB init | 0x1B328 | |
| 追加区 | 0x763A4~0x76423 | flag / stub / usb_log_puts / puts_hook |

### 附：同批逆向的其他结论（速查）

- 模式判定读 AON 寄存器：0x40388EE4（插线/USB 检测，3s）、0x40388EC8（掩码 0xE0，返回 1/2/4）
- boot mode 指针表在 0x6A600~0x6B000：normal / recovery / fastboot / charge / alarm / engtest（含复位原因字符串）
- 符号表：24 B/条 {地址, 0x403, 字符串地址}，分布在 0x6E4A0~0x75820（约 940 条）
- uboot_log 分区 = 4MB：512B 头（magic 0xABCD / data_off 0x200 / ver / entries）+ 6 slot × 256KB；写入者 sub_D1B8
- fastboot：代码在 uboot 内（f_fastboot / usb_fastboot_init），DTB `fastbootbuffer@82000000` 12MB；触发条件为 AON 状态组合

## 7. reference/ 对照样本

另一台 SL8541E 设备的日志，用于评估「改 uboot 会不会变砖」：

- `uart_readable.log` —— UART 采集，8 次启动循环卡在 `uboot_vboot_verify_img() return error`（该固件校验链可用）
- `disavb_tos_8541e.log` —— sfd_tool 刷机日志（含 39 分区表）

结论：不同固件的校验强度不同；DW99 / vp19 本机无可用校验链 → 改 uboot 风险低（且 v1 已实跑验证）。

## 8. 注意事项

- 采集务必「**先起脚本、后上电**」；跳内核断开是正常现象。
- 刷写 uboot 属风险操作，请自行确认分区与打包格式；本仓库不含刷机工具。
- `analysis/uboot.asm` 为 IDA 导出的原版线性反汇编（Input MD5 = `a03efc…`），查地址 / 引用可直接 grep。
- 个人研究存档；固件片段版权归原厂，仅供研究使用。

> 仓库：`https://github.com/Dunoguang/uboot-usblog`
