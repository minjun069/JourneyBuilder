import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        'PB(관리자) 계정을 생성하거나 기존 계정에 PB 권한(is_staff)을 부여한다. '
        '회원가입 화면으로는 PB 계정을 만들 수 없으므로(D-01) 이 커맨드가 유일한 생성 경로다. '
        '비밀번호는 --password 또는 PB_PASSWORD 환경변수로 전달한다.'
    )

    def add_arguments(self, parser):
        parser.add_argument('email', help='PB 계정 이메일')
        parser.add_argument('--name', default='PB', help='표시 이름 (기본: PB)')
        parser.add_argument('--password', help='생략 시 PB_PASSWORD 환경변수를 사용')

    def handle(self, *args, **options):
        User = get_user_model()
        email = options['email']
        password = options['password'] or os.environ.get('PB_PASSWORD')

        user = User.objects.filter(email=email).first()
        if user:
            if user.is_staff and not password:
                self.stdout.write(self.style.WARNING(f'{email} 은 이미 PB 계정입니다.'))
                return
            user.is_staff = True
            if password:
                self._validate(password, user)
                user.set_password(password)
            user.save(update_fields=['is_staff', 'password', 'updated_at'])
            self.stdout.write(self.style.SUCCESS(f'{email} 에 PB 권한을 부여했습니다.'))
            return

        if not password:
            raise CommandError('신규 계정에는 비밀번호가 필요합니다 (--password 또는 PB_PASSWORD).')
        self._validate(password)
        user = User.objects.create_user(email=email, password=password, name=options['name'])
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        self.stdout.write(self.style.SUCCESS(f'PB 계정 {email} 을 생성했습니다.'))

    def _validate(self, password, user=None):
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages))
