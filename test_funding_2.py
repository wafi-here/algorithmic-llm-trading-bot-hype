from hyperliquid.info import Info
from hyperliquid.utils import constants
import json
info = Info(constants.MAINNET_API_URL, skip_ws=True)
meta_and_ctxs = info.meta_and_asset_ctxs()
ctxs = meta_and_ctxs[1]
print(json.dumps([ctx for ctx in ctxs if ctx.get("funding")][:3], indent=2))
