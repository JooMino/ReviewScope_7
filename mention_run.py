import asyncio
from mention_count.dc_counter import run as run_dc



# 관련 dc 언급량 작업을 실행하고 필요한 후속 처리를 수행한다.
async def run_dc_mentions(keyword):
    return await run_dc(keyword, months=48, max_pages=120)

# 관련 clien 언급량 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_clien_mentions(keyword):
    return run_clien_counter(keyword, months=48)


# 관련 언급량 site 작업을 실행하고 필요한 후속 처리를 수행한다.
async def run_mentions_for_site(keyword, site):
    if site == "dc":
        return await run_dc_mentions(keyword)

        return run_clien_mentions(keyword)
    else:
        print(f" 지원 안되는 사이트: {site}")
        return None
    
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("--keyword", required=True)
    parser.add_argument("--site", required=True)

    args = parser.parse_args()

    asyncio.run(
        run_mentions_for_site(
            args.keyword,
            args.site
        )
    )
