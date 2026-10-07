# TVBox / 影视仓 自动更新播放源聚合配置

本仓库用于为影视仓 / TVBox 类播放器提供**自动更新的聚合播放源**。

## 使用方式

在影视仓/ TVBox 的 **设置 → 配置地址** 填入：

```
https://cdn.jsdelivr.net/gh/fswws/tvbox-source@main/tvbox.json
```

填好后 App 每次启动 / 手动刷新会自动拉取最新配置，无需手动维护。

## 文件说明

| 文件 | 说明 |
|---|---|
| `tvbox.json` | 多仓聚合配置（引用影视仓内置源、饭太硬点播源、直播源） |
| `live.json` | 直播源单仓（lives 指向 `live.txt`） |
| `live.txt` | 直播源列表（TVBox txt 格式），由 GitHub Actions 每日自动合并更新 |
| `merge.py` | 直播源合并去重脚本 |
| `.github/workflows/update-live.yml` | 每日自动更新直播源的工作流 |

## 播放源构成

- **点播源**：影视仓内置源（jinenge/tvbox）、饭太硬（qist/tvbox）等稳定接口，上游更新后自动生效
- **直播源**：每日从多个公开直播源仓库（qist/tvbox、fanmingming/live、iptv-org）拉取合并去重

## 手动触发直播源更新

仓库 Actions 页面 → `自动更新直播源` → `Run workflow` 即可立即更新。

> 免责声明：本仓库仅做技术性的源地址聚合与转发，不托管任何视频内容；各源由上游提供，可能随时失效，请自行甄别使用。
