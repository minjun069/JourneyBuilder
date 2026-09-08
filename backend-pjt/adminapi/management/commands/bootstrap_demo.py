"""PB 대시보드 시연 환경 일괄 세팅 — 팀 공용 PB 계정 + 시드 고객.

`create_pb` + `seed_demo` 를 한 번에 실행해 팀원 모두가 동일한 시연 상태를 갖게 한다.

- PB 계정은 시연 편의를 위해 비밀번호 검증을 의도적으로 건너뛴다(약한 비밀번호 허용).
  운영 서버에서는 이 커맨드 대신 `create_pb` 로 강한 비밀번호 계정을 만들 것.
- 오염 방지 가드: 데모(@retirewise.demo)가 아닌 실제 고객 데이터가 이미 있는 DB 에서는
  `--yes` 를 명시해야 실행된다 (운영 DB 실수 실행 방지).
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from .seed_demo import DEMO_DOMAIN, DEMO_PASSWORD

DEFAULT_PB_EMAIL = 'admin1234@naver.com'
DEFAULT_PB_PASSWORD = 'admin1234'


class Command(BaseCommand):
    help = '시연용 PB 계정과 가상 고객 시드를 일괄 생성한다 (로컬 시연 전용).'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=40, help='시드 고객 수 (기본 40)')
        parser.add_argument('--email', default=DEFAULT_PB_EMAIL, help='PB 계정 이메일')
        parser.add_argument('--password', default=DEFAULT_PB_PASSWORD, help='PB 계정 비밀번호')
        parser.add_argument('--name', default='pb', help='PB 표시 이름')
        parser.add_argument(
            '--yes', action='store_true',
            help='데모가 아닌 실제 고객 데이터가 있는 DB 에서도 실행 (운영 오염 방지 가드 해제)',
        )

    def handle(self, *args, **options):
        User = get_user_model()

        real_customers = (
            User.objects
            .filter(is_staff=False)
            .exclude(email__endswith=f'@{DEMO_DOMAIN}')
            .count()
        )
        if real_customers and not options['yes']:
            raise CommandError(
                f'데모 계정이 아닌 고객 {real_customers}명이 이미 있는 DB 입니다. '
                '운영 DB 오염을 막기 위해 중단합니다. 로컬 시연 DB 가 맞으면 --yes 를 붙여 실행하세요.'
            )

        # PB 계정 — 시연 고정 자격증명이므로 존재하면 비밀번호·권한을 재설정해 상태를 통일한다.
        # 비밀번호 검증은 건너뛴다 (시연 전용 약한 비밀번호 허용, 파일 상단 주석 참조).
        user, created = User.objects.get_or_create(
            email=options['email'], defaults={'name': options['name']},
        )
        user.name = options['name']
        user.is_staff = True
        user.set_password(options['password'])
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"PB 계정 {'생성' if created else '재설정'}: {options['email']} / {options['password']}"
        ))

        call_command('seed_demo', count=options['count'])
        self.stdout.write(self.style.SUCCESS(
            f"완료 — PB 1명 + 시드 고객 {options['count']}명 "
            f"(시드 고객 공통 비밀번호: {DEMO_PASSWORD})"
        ))
