# BYD Flash Charge Station Tracker

> **声明：本项目为迪粉个人兴趣统计，非官方项目，与比亚迪公司无关。数据通过公开接口采集，难免存在遗漏或误差，仅供参考。**

追踪比亚迪全国闪充站部署进度，自动采集、地理编码、可视化展示。

目前已收录 **2500+** 闪充站，覆盖 **31** 省 **273** 城市。

## 工作原理

1. **扫描** — 基于全国 3200+ 区县质心 + 城市补充中点 + 高速公路采样点，调用 BYD 充电地图 API 获取站点数据
2. **去重入库** — 按站点 ID 去重，upsert 写入 SQLite，多次扫描自动累积
3. **地理编码** — 离线 Point-in-Polygon（shapely + 省级 GeoJSON）确定站点所属省/市
4. **导出部署** — 导出静态 JSON，通过 Cloudflare Pages 部署前端

## 扫描策略

采用三层扫描点覆盖全国：

| 层级 | 来源 | 数量 | 作用 |
|------|------|------|------|
| 区县质心 | 高德行政区划 API | ~3200 | 基础城市覆盖 |
| 城市补充中点 | 密集城市相邻区间 | ~980 | 防止大城市 API 截断 |
| 高速采样点 | 26 条国家高速每 80km | ~90 | 捕获服务区站点 |

## 数据面板

- 站点总数、闪充桩总数、覆盖城市数、今日新增
- 全国站点散点地图（三级下钻：全国 → 省 → 市）
- 站点增长趋势 + 每日新增柱状图
- 城市站点排行（可搜索、可排序）

## 安装

```bash
conda create -n byd-flashcharge python=3.13
conda activate byd-flashcharge
pip install requests flask shapely
```

复制配置文件并填入高德 API Key（免费申请，5000 次/天）：

```bash
cp config.example.py config.py
# 编辑 config.py，填入你的 AMAP_API_KEY
```

## 使用

### 生成扫描点（首次）

```bash
python scan_points.py
```

从高德 API 获取全国区县质心并生成扫描坐标，缓存到 `data/scan_points.json`。

### 抓取数据

```bash
python scraper.py
```

全量扫描约 2-3 分钟（20 并发），自动入库 + 地理编码。

每次请求使用随机设备标识（imeiMD5），遇到 API 限流时全局退避（所有线程同步暂停），错误汇总输出到日志。

### 启动网页

```bash
python web_server.py
```

打开 http://localhost:5000 查看数据面板。

### 自动化部署（可选）

```bash
# crontab: 每天凌晨 3 点执行，仅在有新增站点时提交
0 3 * * * /path/to/byd-flashcharge/deploy.sh
```

## 项目结构

```
byd-flashcharge/
├── config.example.py      # 配置模板（不含密钥）
├── config.py              # 实际配置（git 忽略）
├── scraper.py             # 数据抓取（并发扫描 + 全局退避 + 自动重试）
├── scan_points.py         # 扫描点生成（区县质心 + 中点 + 高速）
├── geocoder.py            # 地理编码（离线 PiP + 高德 API 备用）
├── database.py            # SQLite 数据库操作
├── export_json.py         # 导出静态 JSON
├── web_server.py          # Flask Web 服务
├── deploy.sh              # 自动化部署脚本
├── download_maps.py       # 下载省级 GeoJSON
├── public/                # Cloudflare Pages 静态站
│   ├── index.html
│   └── api/*.json         # 导出的站点数据
├── templates/             # Flask 模板
└── data/                  # 运行时数据（git 忽略）
    ├── stations.db        # SQLite 数据库
    ├── scan_points.json   # 扫描点缓存
    └── scraper.log        # 运行日志
```

## License

MIT
