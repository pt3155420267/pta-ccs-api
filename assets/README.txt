本地图片资源目录(CDP 布局),由 pintia-ccs-api 对外提供下载:

  contest/logo.<ext>                  比赛图标(Resolver 主页面图片,也是无照片队伍的默认照片)
  organizations/<orgId>/logo.<ext>    机构图标(该机构所有队伍共享)
  teams/<teamId>/photo.<ext>          队伍照片(teamId 为队伍 API id,可简写为数字 uid)

修改/新增图片后重启本程序即可生效(仅重建本地事件日志,不会重新请求 PTA)。
