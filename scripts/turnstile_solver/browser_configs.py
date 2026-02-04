import random

class browser_config:
    @staticmethod
    def get_random_browser_config(browser_type):
        # 返回: 浏览器名, 版本, User-Agent, Sec-CH-UA
        versions = ["120.0.0.0", "121.0.0.0", "122.0.0.0", "124.0.0.0"]
        ver = random.choice(versions)
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        sec_ch_ua = f'"Not(A:Brand";v="99", "Google Chrome";v="{ver.split(".")[0]}", "Chromium";v="{ver.split(".")[0]}"'
        return "chrome", ver, ua, sec_ch_ua

    @staticmethod
    def get_browser_config(name, version):
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
        sec_ch_ua = f'"Google Chrome";v="{version}", "Chromium";v="{version}"'
        return ua, sec_ch_ua