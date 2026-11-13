# 人格调度

> 正常对话 / 酒馆 / 融合 三模式一键切换，世界书按模式注入

## 这是什么

把「酒馆模式」和「正常对话」做成可切换的开关，再加一档「融合模式」。normal 只用 AstrBot 现有 persona、不注入任何世界书；tavern 注入完整世界书（14 条）由角色卡世界观驱动；blend 保留 persona 并注入精简世界书（剔除与 persona 重复的角色档案）。世界书只走 req.extra_user_content_parts + mark_as_temp，不改 system_prompt、不落历史；每个模式绑定独立 conversation，切模式即切上下文，互不污染。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/xiaoli0412/astrbot_plugin_persona_conductor.git
```

然后在 WebUI 插件管理里启用，或重启 AstrBot。

## 配置

全部配置都能在 **WebUI → 插件 → 人格调度 → 配置** 里改，不用手改文件。

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `True` | 总开关。关闭后 /模式 仍可切换（仅记录状态），但不会注入任何世界书。 |
| `default_mode` | string | `normal` | 新会话（state.json 里没有记录的 unified_msg_origin）使用的模式。normal=只用 persona 不注入；t |
| `inject_priority` | int | `200` | on_llm_request 的 handler 优先级，数值越大越先执行。默认 200：晚于 livingmemory(0)、早于 sti |
| `max_worldbook_chars` | int | `24000` | 超出即截断并追加截断标记。完整世界书约 1.99 万字，留了余量；调小可省 token。 |
| `tavern_exclude_uids` | string | `` | 逗号分隔，如 015。酒馆模式一般留空（要全量）。 |
| `blend_exclude_uids` | string | `015` | 逗号分隔。默认排除 015（人物A的深度档案），因为它与 AstrBot 现有 persona 高度重复；可改成其它 uid 或留空。 |
| `inject_hint` | bool | `True` | 在世界书块后面追加一句：以上是本轮场景的世界设定……不要复述这些设定，像平常一样说话。 |
| `log_inject` | bool | `True` | 每次注入在日志里打一行：mode / 条目数 / 字数 / umo，便于排查。 |

## 依赖

无第三方依赖（纯标准库）

## 兼容性

- 平台：*
- AstrBot：`>=4.24,<5`

## 隐私说明

插件只在本机运行，不上传任何数据到第三方。配置与运行时数据存在 `AstrBot/data/` 下。

## 许可

MIT，见 [LICENSE](LICENSE)。
