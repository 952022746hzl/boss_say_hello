import re
from dataclasses import dataclass
from boss_say_hello.domain import CheckResult


@dataclass(frozen=True)
class SalaryRange:
    low: int
    high: int


def parse_salary(raw: str) -> SalaryRange | None:
    value = re.sub(r'\s+', '', raw).replace('—','-').replace('–','-').replace('~','-').replace('～','-')
    value = re.sub(r'[·•]\d+薪$', '', value)
    match = re.fullmatch(r'(\d+(?:\.\d+)?)([kK万千元]?)-(\d+(?:\.\d+)?)([kK万千元])(?:/月|月薪)?', value)
    if not match:
        return None
    left, left_unit, right, right_unit = match.groups()
    factors = {'k':1000,'K':1000,'千':1000,'万':10000,'元':1}
    low, high = int(float(left)*factors[left_unit or right_unit]), int(float(right)*factors[right_unit])
    return SalaryRange(low,high) if 0 <= low <= high else None


def city_name(value: str) -> str:
    return value.strip().removesuffix('市')


def check_job(config, job) -> CheckResult:
    if not job.id or not job.account_id:
        return CheckResult('pending', ('岗位或账号标识不明确',))
    if job.contact_status == 'contacted':
        return CheckResult('reject', ('已联系',))
    if job.contact_status != 'new':
        return CheckResult('pending', ('沟通状态不明确',))
    if not job.text.strip():
        return CheckResult('pending', ('岗位正文缺失',))
    pending = []
    if config.cities:
        if not job.city or '远程' in job.city:
            pending.append('城市待确认')
        elif city_name(job.city) not in {city_name(city) for city in config.cities}:
            return CheckResult('reject', ('城市不符合配置',))
    salary = parse_salary(job.salary_raw)
    if salary is None:
        pending.append('薪资面议或无法解析，待确认')
    elif ((config.salary_min is not None and salary.high < config.salary_min) or
          (config.salary_max is not None and salary.low > config.salary_max)):
        return CheckResult('reject', ('薪资区间没有交集',))
    return CheckResult('pending',tuple(pending)) if pending else CheckResult('pass')
