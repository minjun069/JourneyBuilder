from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

ADMIN_PING = '/api/v1/admin/ping/'


def bearer_client(user):
    client = APIClient()
    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


class AdminAccessControlTests(TestCase):
    """NFR-01: 관리자 API는 서버에서 IsAdminUser로 이중 방어한다."""

    @classmethod
    def setUpTestData(cls):
        cls.pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        cls.pb.is_staff = True
        cls.pb.save(update_fields=['is_staff'])
        cls.customer = User.objects.create_user(
            email='customer@test.com', password='cust-pass-1234', name='고객'
        )

    def test_pb_token_gets_200(self):
        response = bearer_client(self.pb).get(ADMIN_PING)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_customer_token_gets_403(self):
        response = bearer_client(self.customer).get(ADMIN_PING)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_401(self):
        response = APIClient().get(ADMIN_PING)
        self.assertEqual(response.status_code, 401)


class CreatePbCommandTests(TestCase):
    def test_creates_new_pb_account(self):
        call_command('create_pb', 'new-pb@test.com', '--name', '김PB', '--password', 'pb-pass-1234')
        user = User.objects.get(email='new-pb@test.com')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password('pb-pass-1234'))
        self.assertEqual(user.name, '김PB')

    def test_promotes_existing_customer(self):
        User.objects.create_user(email='cust@test.com', password='cust-pass-1234', name='고객')
        call_command('create_pb', 'cust@test.com')
        self.assertTrue(User.objects.get(email='cust@test.com').is_staff)

    def test_new_account_requires_password(self):
        with self.assertRaises(CommandError):
            call_command('create_pb', 'no-pass@test.com')

    def test_rejects_weak_password(self):
        with self.assertRaises(CommandError):
            call_command('create_pb', 'weak@test.com', '--password', '1234')
