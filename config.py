# BYD Flash Charge Station Tracker - Configuration

API_URL = "https://chargeapp-cn.byd.auto/chargeMap/operator-server/app/V1/station/searchNearby"

# Request template (from captured HAR)
REQUEST_HEADERS = {
    "user-agent": "libcurl-agent/1.0",
    "accept": "*/*",
    "accept-encoding": "identity",
    "content-type": "application/json; charset=utf-8",
    "platform": "HARMONY",
    "softtype": "0",
    "version": "112",
}

# These fields stay constant (imeiMD5 and sign removed — blocked by server)
REQUEST_TEMPLATE = {
    "appChannel": "11",
    "identifier": "288253498326462464",
    "mapCode": "amap",
    "vehicleChargeType": 0,
    "orderBy": "general",
}

# Concurrent workers for parallel API requests
CONCURRENT_WORKERS = 30

# Amap (高德地图)
AMAP_API_KEY = "eec97f01beba5127aaf51661d72b92d3"

# Database
DB_PATH = "data/stations.db"
DATA_DIR = "data"
