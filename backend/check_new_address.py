from hyperliquid.info import Info
import pprint

def main():
    base_url = "https://api.hyperliquid.xyz"
    address = "0x3c8A2241fE1B98Ecf14DF3F0B8889Fc8c7f24067"
    
    print(f"Querying Hyperliquid Mainnet state for {address}...")
    try:
        info = Info(base_url, skip_ws=True)
        user_state = info.user_state(address)
        print("\n--- PERP ACCOUNT ---")
        pprint.pprint(user_state)
        
        spot_state = info.spot_user_state(address)
        print("\n--- SPOT ACCOUNT ---")
        pprint.pprint(spot_state)
    except Exception as e:
        print(f"FAILED on Mainnet: {str(e)}")
        
    print(f"\nQuerying Hyperliquid Testnet state for {address}...")
    try:
        info = Info("https://api.hyperliquid-testnet.xyz", skip_ws=True)
        user_state = info.user_state(address)
        print("\n--- TESTNET PERP ACCOUNT ---")
        pprint.pprint(user_state)
    except Exception as e:
        print(f"FAILED on Testnet: {str(e)}")

if __name__ == "__main__":
    main()
