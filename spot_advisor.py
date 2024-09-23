import requests
import json
import argparse
from prettytable import PrettyTable
from concurrent.futures import ThreadPoolExecutor, as_completed


SPOT_ADVISOR_URL = "https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json"
SPOT_PRICE_URL = "http://spot-price.s3.amazonaws.com/spot.js"

ranges = [
    {"index": 0, "label": "<5%", "dots": 0, "max": 5},
    {"index": 1, "label": "5-10%", "dots": 1, "max": 11},
    {"index": 2, "label": "10-15%", "dots": 2, "max": 16},
    {"index": 3, "label": "15-20%", "dots": 3, "max": 22},
    {"index": 4, "label": ">20%", "dots": 4, "max": 100}
]

def fetch_spot_price(instance_type, region, os):
    response = requests.get(SPOT_PRICE_URL)

    if response.status_code == 200:
        raw_data = response.text.strip("callback(").rstrip(");")
        spot_data = json.loads(raw_data)
        regions_data = spot_data.get("config", {}).get("regions", [])

        for region_data in regions_data:
            if region_data.get("region") == region:
                instance_types = region_data.get("instanceTypes", [])

                for instance_type_data in instance_types:
                    sizes = instance_type_data.get("sizes", [])

                    for size in sizes:
                        if size.get("size") == instance_type:
                            price = next(
                                (vc['prices']['USD'] for vc in size.get("valueColumns", []) if vc["name"] == os),
                                "N/A"
                            )
                            return price
    return None

def get_filtered_instances(min_cores, max_cores, min_ram, max_ram, max_interruption, region, max_usd, instance_types=None):
    response = requests.get(SPOT_ADVISOR_URL)

    if response.status_code == 200:
        data = json.loads(response.text)
        instance_types_data = data.get("instance_types", {})
        spot_advisor = data.get("spot_advisor", {})
        filtered_instances = []

        for it_type, details in instance_types_data.items():
            if instance_types and not any(it in it_type for it in instance_types):
                continue

            cores = details.get("cores")
            ram_gb = details.get("ram_gb")

            if not (min_cores <= cores <= max_cores and min_ram <= ram_gb <= max_ram):
                continue

            if region in spot_advisor:
                region_data = spot_advisor[region]
                linux_data = region_data.get("Linux", {})

                if it_type in linux_data:
                    discount = linux_data[it_type].get("s")
                    r_value = linux_data[it_type].get("r")
                    freq_label = next((r['label'] for r in ranges if r['index'] == r_value), "Unknown")

                    if max_interruption is not None:
                        r_max = next((r['max'] for r in ranges if r['index'] == r_value), 100)
                        if r_max > max_interruption:
                            continue

                    filtered_instances.append({
                        "instance_type": it_type,
                        "cores": cores,
                        "ram": ram_gb,
                        "discount": discount,
                        "frequency_of_interruption": freq_label
                    })

        return filtered_instances
    else:
        print(f"Failed to retrieve Spot Advisor data: {response.status_code}")
        return []

def display_instance_info(instances, region, os, max_usd):
    table = PrettyTable()
    table.field_names = ["INSTANCE", "vCPU", "Memory (GB)", "Saving On On-Demand (%)", "Frequency of Interruption", "USD/h"]

    with ThreadPoolExecutor() as executor:
        future_to_instance = {executor.submit(fetch_spot_price, instance["instance_type"], region, os): instance for instance in instances}

        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                price = future.result()
                if price is not None and price != "N/A":
                    price = float(price)
                    if max_usd is not None and price > max_usd:
                        continue  # 가격이 제한을 초과하면 제외
            except Exception as exc:
                print(f"Error fetching price for {instance['instance_type']}: {exc}")
                price = "N/A"

            table.add_row([
                instance["instance_type"], instance["cores"], instance["ram"],
                instance["discount"], instance["frequency_of_interruption"], price or "N/A"
            ])


    print(table)


def main():
    parser = argparse.ArgumentParser(description="Filter EC2 instances by cores, RAM, region, interruption, and price")
    parser.add_argument('--min-cores', type=int, default=0, help='Minimum number of cores')
    parser.add_argument('--max-cores', type=int, default=float('inf'), help='Maximum number of cores')
    parser.add_argument('--min-ram', type=float, default=0.0, help='Minimum amount of RAM in GB')
    parser.add_argument('--max-ram', type=float, default=float('inf'), help='Maximum amount of RAM in GB')
    parser.add_argument('--max-interruption', type=int, help='Maximum Frequency of Interruption (%)')
    parser.add_argument('--region', type=str, required=True, help='Region for spot advisor discount')
    parser.add_argument('--os', type=str, choices=['linux', 'windows'], required=True, help='Operating System (linux or windows)')
    parser.add_argument('--instance-type', type=str, help='Comma-separated list of instance types or families (e.g., "6i,7i")')
    parser.add_argument('--max-usd', type=float, help='Maximum price in USD per hour')

    args = parser.parse_args()

    instance_types = args.instance_type.split(",") if args.instance_type else None

    filtered_instances = get_filtered_instances(args.min_cores, args.max_cores, args.min_ram, args.max_ram, args.max_interruption, args.region, args.max_usd, instance_types)

    if filtered_instances:
        display_instance_info(filtered_instances, args.region, args.os, args.max_usd)
    else:
        print("No instances match the given criteria.")

if __name__ == "__main__":
    main()