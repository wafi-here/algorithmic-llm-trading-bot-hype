from hyperliquid.info import Info
from hyperliquid.utils import constants
info = Info(constants.MAINNET_API_URL, skip_ws=True)
meta = info.meta()
print([asset for asset in meta["universe"] if asset["name"] in ["BTC", "ETH", "SOL"]])
