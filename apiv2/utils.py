# -*- coding: utf-8 -*-

#
# Freesound is (c) MUSIC TECHNOLOGY GROUP, UNIVERSITAT POMPEU FABRA
#
# Freesound is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Freesound is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     See AUTHORS file.
#

from provider.views import OAuthError
from provider.scope import to_names, to_int
from provider.oauth2.views import AccessTokenView as DjangoRestFrameworkAccessTokenView, Authorize as DjangoOauth2ProviderAuthorize
from provider.oauth2.forms import PasswordGrantForm
from provider.oauth2.models import RefreshToken, AccessToken
from rest_framework.generics import GenericAPIView as RestFrameworkGenericAPIView, ListAPIView as RestFrameworkListAPIView, RetrieveAPIView as RestFrameworkRetrieveAPIView
from exceptions import UnauthorizedException
from apiv2.authentication import OAuth2Authentication, TokenAuthentication, SessionAuthentication
from sounds.models import Sound, Pack, License
from freesound.utils.audioprocessing import get_sound_type
from geotags.models import GeoTag
from freesound.utils.filesystem import md5file
from freesound.utils.text import slugify
from exceptions import ServerErrorException, OtherException
import shutil
import settings
import os


class AccessTokenView(DjangoRestFrameworkAccessTokenView):

    '''
    We override only a function of the AccessTokenView class in order to be able to set different
    allowed grant types per API client and to resctrict scopes on a client basis.
    '''

    def get_password_grant(self, request, data, client):
        if not client.apiv2_client.allow_oauth_passoword_grant:
            raise OAuthError({'error': 'unsupported_grant_type'})

        form = PasswordGrantForm(data, client=client)
        if not form.is_valid():
            raise OAuthError(form.errors)
        return form.cleaned_data

    def refresh_token(self, request, data, client):
        """
        Handle ``grant_type=refresh_token`` requests as defined in :draft:`6`.
        We overwrite this function so that old access tokens are deleted when refreshed. Otherwise multiple access tokens
        can be created, leading to errors.
        """
        rt = self.get_refresh_token_grant(request, data, client)

        #self.invalidate_refresh_token(rt)
        #self.invalidate_access_token(rt.access_token)
        scope = rt.access_token.scope
        rt.access_token.delete()

        at = self.create_access_token(request, rt.user, scope, client)
        rt.delete()
        rt = self.create_refresh_token(request, at.user, at.scope, at, client)

        return self.access_token_response(at)

    def create_access_token(self, request, user, scope, client):

        # Filter out requested scopes and only leave those allowed to the client
        client_scope = client.apiv2_client.get_scope_display()
        allowed_scopes = [requested_scope for requested_scope in to_names(scope) if requested_scope in client_scope]

        return AccessToken.objects.create(
            user=user,
            client=client,
            scope=to_int(*allowed_scopes)
        )

    def create_refresh_token(self, request, user, scope, access_token, client):

        return RefreshToken.objects.create(
            user=user,
            access_token=access_token,
            client=client
        )


def get_authentication_details_form_request(request):
    auth_method_name = None
    user = None
    developer = None

    if request.successful_authenticator:
        auth_method_name = request.successful_authenticator.authentication_method_name
        if auth_method_name == "OAuth2":
            user = request.user
            developer = request.auth.client.user
        elif auth_method_name == "Token":
            user = None
            developer = request.auth.user
        elif auth_method_name == "Session":
            user = request.user
            developer = None

    return auth_method_name, developer, user


class Authorize(DjangoOauth2ProviderAuthorize):
    if settings.USE_MINIMAL_TEMPLATES_FOR_OAUTH:
        template_name = 'api/minimal_authorize_app.html'
    else:
        template_name = 'api/authorize_app.html'


class WriteRequiredGenericAPIView(RestFrameworkGenericAPIView):
    authentication_classes = (OAuth2Authentication, SessionAuthentication)

    def initial(self, request, *args, **kwargs):
        super(WriteRequiredGenericAPIView, self).initial(request, *args, **kwargs)

        # Get request informationa dn store it as class variable
        self.auth_method_name, self.developer, self.user = get_authentication_details_form_request(request)

        # Check if client has write permissions
        if self.auth_method_name == "OAuth2":
            if "write" not in request.auth.client.apiv2_client.get_scope_display():
                raise UnauthorizedException


class ListAPIView(RestFrameworkListAPIView):
    authentication_classes = (OAuth2Authentication, TokenAuthentication, SessionAuthentication)

    def initial(self, request, *args, **kwargs):
        super(ListAPIView, self).initial(request, *args, **kwargs)

        # Get request informationa dn store it as class variable
        self.auth_method_name, self.developer, self.user = get_authentication_details_form_request(request)


class RetrieveAPIView(RestFrameworkRetrieveAPIView):
    authentication_classes = (OAuth2Authentication, TokenAuthentication, SessionAuthentication)

    def initial(self, request, *args, **kwargs):
        super(RetrieveAPIView, self).initial(request, *args, **kwargs)

        # Get request informationa dn store it as class variable
        self.auth_method_name, self.developer, self.user = get_authentication_details_form_request(request)


class GenericAPIView(RestFrameworkGenericAPIView):
    authentication_classes = (OAuth2Authentication, TokenAuthentication, SessionAuthentication)

    def initial(self, request, *args, **kwargs):
        super(GenericAPIView, self).initial(request, *args, **kwargs)

        # Get request informationa dn store it as class variable
        self.auth_method_name, self.developer, self.user = get_authentication_details_form_request(request)


def create_sound_object(user, sound_fields):

    # 1 prepare some variable names
    filename = sound_fields['upload_filename']
    if not sound_fields['name']:
        sound_fields['name'] = filename

    directory = os.path.join(settings.UPLOADS_PATH, str(user.id))
    dest_path = os.path.join(directory, filename)

    # 2 make sound object
    sound = Sound()
    sound.user = user
    sound.original_filename = sound_fields['name']
    sound.original_path = dest_path
    sound.filesize = os.path.getsize(sound.original_path)
    sound.type = get_sound_type(sound.original_path)
    license = License.objects.get(name=sound_fields['license'])
    sound.license = license

    # 3 md5, check
    try:
        sound.md5 = md5file(sound.original_path)
    except IOError:
        if settings.DEBUG:
            msg = "Md5 could not be computed."
        else:
            msg = "Server error."
        raise ServerErrorException(msg=msg)

    sound_already_exists = Sound.objects.filter(md5=sound.md5).exists()
    if sound_already_exists:
        os.remove(sound.original_path)
        raise OtherException("Sound could not be created because the uploaded file is already part of freesound.")

    # 4 save
    sound.save()

    # 5 move to new path
    orig = os.path.splitext(os.path.basename(sound.original_filename))[0]  # WATCH OUT!
    sound.base_filename_slug = "%d__%s__%s" % (sound.id, slugify(sound.user.username), slugify(orig))
    new_original_path = sound.locations("path")
    if sound.original_path != new_original_path:
        try:
            os.makedirs(os.path.dirname(new_original_path))
        except OSError:
            pass
        try:
            shutil.move(sound.original_path, new_original_path)
        except IOError, e:
            if settings.DEBUG:
                msg = "File could not be copied to the correct destination."
            else:
                msg = "Server error."
            raise ServerErrorException(msg=msg)
        sound.original_path = new_original_path
        sound.save()

    # 6 create pack if it does not exist
    if sound_fields['pack']:
        if Pack.objects.filter(name=sound_fields['pack'], user=user).exists():
            p = Pack.objects.get(name=sound_fields['pack'], user=user)
        else:
            p, created = Pack.objects.get_or_create(user=user, name=sound_fields['pack'])

        sound.pack = p

    # 7 create geotag objects
    # format: lat#lon#zoom
    if sound_fields['geotag']:
        lat, lon, zoom = sound_fields['geotag'].split(',')
        geotag = GeoTag(user=user,
            lat=float(lat),
            lon=float(lon),
            zoom=int(zoom))
        geotag.save()
        sound.geotag = geotag

    # 8 set description, tags
    sound.description = sound_fields['description']
    sound.set_tags([t.lower() for t in sound_fields['tags'].split(" ") if t])

    # 9 save!
    sound.save()

    # 10 Proces
    try:
        sound.process()
    except Exception, e:
        pass

    # Set moderation state to OK (this is just for testing)
    #sound.moderation_state = 'OK'
    #sound.processing_state = 'OK'
    #sound.save()

    return sound