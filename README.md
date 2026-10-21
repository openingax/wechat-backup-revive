# wechat-backup-revive

让 macOS 微信 4.x「备份与恢复」重新认出旧的聊天记录备份，并恢复到手机或 iPad。适用于换了手机或电脑、挪过备份目录、外部存储设备备份之后"找不到备份"的情况。

- 📖 案例全过程：[docs/case-study.md](docs/case-study.md)
- 🧰 Claude Code skill：[skills/wechat-backup-revive](skills/wechat-backup-revive/SKILL.md)
- 🐍 工具：`skills/wechat-backup-revive/scripts/wxbackup.py`，只依赖 Python 3 标准库

## 安装 skill

```bash
git clone https://github.com/openingax/wechat-backup-revive.git
cp -R wechat-backup-revive/skills/wechat-backup-revive ~/.claude/skills/
```

之后对 Claude Code 说"帮我恢复旧的微信备份"即可。

## 不用 Claude 直接用

```bash
python3 -I wxbackup.py selftest
python3 -I wxbackup.py inspect <备份的 hash 目录>
```

完整步骤见 [SKILL.md](skills/wechat-backup-revive/SKILL.md)。

## 免责

只用于恢复你自己账号的备份。全程不解密任何数据，只调整备份的目录结构和明文登记信息，最后由微信官方客户端完成恢复。请只在副本上操作，并保留原件。只在 macOS 微信 4.1.13 上验证过。与腾讯无关。

MIT License
