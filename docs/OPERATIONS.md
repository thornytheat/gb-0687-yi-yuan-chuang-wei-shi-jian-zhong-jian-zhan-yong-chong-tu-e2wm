# 运行维护

主服务端口与Compose保持一致。数据保存在records命名卷，应用代码不需要写入权限。

```sh
docker compose logs --tail=80 app
docker compose restart app
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

健康检查GET /health。失败输入返回原因码，结构性错误422，过大文件413，陈旧复核409。
文件内容最大4MB。批次先计算再原子保存，失败不留半条记录。证据CSV按原始行号定位。

## 编号对应关系

对应关系表 identity_links 与运行批次同库，只增不删。GET /api/identity/links 返回 active 与全部历史；POST 提交 er_patient/ip_patient（可带 run_id 做编号来源校验、note 备注），同对编号重试返回 200 且 created=false，不新增行；更正返回 201 且新行 replaces 指向旧行。POST /api/identity/links/<link_id>/unlink 撤销生效对应。
LINK_UNKNOWN 表示编号不在批次事件或编号域不符；LINK_CONFLICT 表示两个编号各有不同生效对应，事务回滚不留半条。已封存批次不被修改：M8 页面用封存的原始输入即时补算身份接回。
