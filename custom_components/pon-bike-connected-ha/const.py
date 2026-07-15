DOMAIN = "pon_bike_connected_ha"
NAME = "PON Bike Connected"

AUTHORIZE_URL = "https://consumer.login.pon.bike/authorize"
TOKEN_URL = "https://consumer.login.pon.bike/oauth/token"

API_BASE = "https://data-act.connected.pon.bike/api"

DEFAULT_SCAN_INTERVAL = 300

PLATFORMS: list[str] = ["sensor", "device_tracker", "binary_sensor"]

# MQTT broker settings
MQTT_BROKER = "data-act.connected.pon.bike"
MQTT_PORT = 8883
MQTT_TOPIC_PREFIX = "data-act"
# Refresh the MQTT connection every 50 minutes so the OAuth token never expires mid-session
MQTT_TOKEN_REFRESH_INTERVAL = 3000

