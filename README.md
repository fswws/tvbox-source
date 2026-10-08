# TVBox 自动更新源

影视仓 / TVBox 可用的自动更新播放源仓库。每 **3 天**由 GitHub Actions 自动拉取最新可用源并刷新缓存，保证地址始终可用。

## 使用方式（影视仓/TVBox）

| 设置项 | 填写地址 |
|---|---|
| **配置地址** | `https://fastly.jsdelivr.net/gh/fswws/tvbox-source@main/tvbox.json` |
| **直播地址** | `https://fastly.jsdelivr.net/gh/fswws/tvbox-source@main/live.json` |

> 配置地址为「多仓」聚合列表，也可以直接把 `vod/dom_1.json` 等单仓地址填入（单仓/多仓均可，应用会自动识别）。

## 更新机制

- 工作流 `.github/workflows/update-live.yml` 每 3 天（北京时间 06:30）自动运行一次，也可手动 `workflow_dispatch` 触发。
- `update_sources.py` 从 `sources.json` 候选池拉取各源，逐项校验（配置可解析、站点 ≥3 个；直播列表非空且含频道），将可用源缓存到本仓库，并重新生成 `tvbox.json` 与 `live.json`。
- 国内点播、国内直播默认各保留 **5 个**最新可用源（首次构建为 10 个）；海外点播、海外直播各 **5 个**，随每次更新同步刷新。
- 所有对外地址统一走 `fastly.jsdelivr.net` 加速，国内网络可直接访问。

## 目录结构

```
tvbox.json        多仓聚合配置（自动生成）
live.json         直播源配置（自动生成）
sources.json      候选源池（手动维护，增删候选改这里）
update_sources.py 更新脚本（自动生成上述配置）
vod/              点播源缓存（dom_1~N.json 国内；oversea_1~5.json 海外）
live/             直播源缓存（dom_1~N.* 国内；oversea_1~5.* 海外）
last_update.json  最近一次更新汇总
```

## 注意事项

- 播放源均为社区公开接口，可能随时变化；每次更新都会重新校验并替换失效源。
- 本仓库仅做技术聚合与缓存，不保证任何第三方源长期可用，请遵守相关法律法规。
