from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

ME_URL = '/api/v1/auth/me/'


class MeViewTests(TestCase):
    """FR-01: 로그인 후 /auth/me/ 로 is_staff를 확인한다."""

    def _client_for(self, user):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return client

    def test_returns_profile_for_customer(self):
        user = User.objects.create_user(email='customer@test.com', password='cust-pass-1234', name='고객')
        response = self._client_for(user).get(ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'id': user.id, 'email': 'customer@test.com', 'name': '고객', 'is_staff': False},
        )

    def test_returns_is_staff_true_for_pb(self):
        pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        pb.is_staff = True
        pb.save(update_fields=['is_staff'])
        response = self._client_for(pb).get(ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['is_staff'])

    def test_requires_authentication(self):
        response = APIClient().get(ME_URL)
        self.assertEqual(response.status_code, 401)

    def test_does_not_leak_sensitive_fields(self):
        # NFR-02: 인증·연락처 필드는 응답에 포함하지 않는다.
        user = User.objects.create_user(
            email='priv@test.com', password='priv-pass-1234', name='고객', phone='010-0000-0000'
        )
        body = self._client_for(user).get(ME_URL).json()
        self.assertEqual(set(body.keys()), {'id', 'email', 'name', 'is_staff'})
