import datetime
import random
import re

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.hashcompat import sha_constructor

from couchdbkit.ext.django.schema import *
from django_couchdb_utils.auth import User


SHA1_RE = re.compile('^[a-f0-9]{40}$')


def activate_user(activation_key):
    """
    Validate an activation key and activate the corresponding
    ``User`` if valid.
    
    If the key is valid and has not expired, return the ``User``
    after activating.
    
    If the key is not valid or has expired, return ``False``.
    
    If the key is valid but the ``User`` is already active,
    return ``False``.
    
    To prevent reactivation of an account which has been
    deactivated by site administrators, the activation key is
    reset to the string constant ``RegistrationProfile.ACTIVATED``
    after successful activation.

    """
    # Make sure the key we're trying conforms to the pattern of a
    # SHA1 hash; if it doesn't, no point trying to look it up in
    # the database.
    if not SHA1_RE.search(activation_key):
        return False
    try:
        r = User.view('registration/users_by_activation_key', key=activation_key, include_docs=True)
        if not r:
            return False
        user = r.first()
    if not user.activation_key_expired():
        del user.activation_key
        user.is_active = True
        user.save()
        return user

def create_inactive_user(username, email, password,
                         site, send_email=True):
    """
    Create a new, inactive ``User``, generate a
    ``RegistrationProfile`` and email its activation key to the
    ``User``, returning the new ``User``.

    By default, an activation email will be sent to the new
    user. To disable this, pass ``send_email=False``.
    
    """
    new_user = User()
    new_user.username = username
    new_user.email = email
    new_user.set_password(password)
    new_user.is_active = False
    new_user.save()

    create_profile(new_user)

    if send_email:
        user.send_activation_email(site)

    return new_user


def create_profile(user):
    """
    Create a ``RegistrationProfile`` for a given
    ``User``, and return the ``RegistrationProfile``.
    
    The activation key for the ``RegistrationProfile`` will be a
    SHA1 hash, generated from a combination of the ``User``'s
    username and a random salt.
    
    """
    salt = sha_constructor(str(random.random())).hexdigest()[:5]
    username = user.username
    if isinstance(username, unicode):
        username = username.encode('utf-8')
    user.activation_key = sha_constructor(salt+username).hexdigest()

    
def delete_expired_users():
    """
    Remove expired instances of ``RegistrationProfile`` and their
    associated ``User``s.
    
    Accounts to be deleted are identified by searching for
    instances of ``RegistrationProfile`` with expired activation
    keys, and then checking to see if their associated ``User``
    instances have the field ``is_active`` set to ``False``; any
    ``User`` who is both inactive and has an expired activation
    key will be deleted.
    
    It is recommended that this method be executed regularly as
    part of your routine site maintenance; this application
    provides a custom management command which will call this
    method, accessible as ``manage.py cleanupregistration``.
    
    Regularly clearing out accounts which have never been
    activated serves two useful purposes:
    
    1. It alleviates the ocasional need to reset a
       ``RegistrationProfile`` and/or re-send an activation email
       when a user does not receive or does not act upon the
       initial activation email; since the account will be
       deleted, the user will be able to simply re-register and
       receive a new activation key.
    
    2. It prevents the possibility of a malicious user registering
       one or more accounts and never activating them (thus
       denying the use of those usernames to anyone else); since
       those accounts will be deleted, the usernames will become
       available for use again.
    
    If you have a troublesome ``User`` and wish to disable their
    account while keeping it in the database, simply delete the
    associated ``RegistrationProfile``; an inactive ``User`` which
    does not have an associated ``RegistrationProfile`` will not
    be deleted.
    
    """
    for user in User.all_users():
        if user.activation_key_expired():
            if not user.is_active:
                user.delete()


class RegistrationUser(User):
    """
    A simple profile which stores an activation key for use during
    user account registration.
    
    Generally, you will not want to interact directly with instances
    of this model; the provided manager includes methods
    for creating and activating new accounts, as well as for cleaning
    out accounts which have never been activated.
    
    While it is possible to use this model as the value of the
    ``AUTH_PROFILE_MODULE`` setting, it's not recommended that you do
    so. This model's sole purpose is to store data temporarily during
    account registration and activation.
    
    """
    activation_key = StringProperty()
    
    
    def activation_key_expired(self):
        """
        Determine whether this ``RegistrationProfile``'s activation
        key has expired, returning a boolean -- ``True`` if the key
        has expired.
        
        Key expiration is determined by a two-step process:
        
        1. If the user has already activated, the key will have been
           reset to the string constant ``ACTIVATED``. Re-activating
           is not permitted, and so this method returns ``True`` in
           this case.

        2. Otherwise, the date the user signed up is incremented by
           the number of days specified in the setting
           ``ACCOUNT_ACTIVATION_DAYS`` (which should be the number of
           days after signup during which a user is allowed to
           activate their account); if the result is less than or
           equal to the current date, the key has expired and this
           method returns ``True``.
        
        """
        expiration_date = datetime.timedelta(days=settings.ACCOUNT_ACTIVATION_DAYS)
        return getattr(self, 'activation_key', False) or \
               (self.date_joined + expiration_date <= datetime.datetime.now())
    activation_key_expired.boolean = True

    def send_activation_email(self, site):
        """
        Send an activation email to the user associated with this
        ``RegistrationProfile``.
        
        The activation email will make use of two templates:

        ``registration/activation_email_subject.txt``
            This template will be used for the subject line of the
            email. Because it is used as the subject line of an email,
            this template's output **must** be only a single line of
            text; output longer than one line will be forcibly joined
            into only a single line.

        ``registration/activation_email.txt``
            This template will be used for the body of the email.

        These templates will each receive the following context
        variables:

        ``activation_key``
            The activation key for the new account.

        ``expiration_days``
            The number of days remaining during which the account may
            be activated.

        ``site``
            An object representing the site on which the user
            registered; depending on whether ``django.contrib.sites``
            is installed, this may be an instance of either
            ``django.contrib.sites.models.Site`` (if the sites
            application is installed) or
            ``django.contrib.sites.models.RequestSite`` (if
            not). Consult the documentation for the Django sites
            framework for details regarding these objects' interfaces.

        """
        ctx_dict = {'activation_key': self.activation_key,
                    'expiration_days': settings.ACCOUNT_ACTIVATION_DAYS,
                    'site': site}
        subject = render_to_string('registration/activation_email_subject.txt',
                                   ctx_dict)
        # Email subject *must not* contain newlines
        subject = ''.join(subject.splitlines())
        
        message = render_to_string('registration/activation_email.txt',
                                   ctx_dict)
        
        self.email_user(subject, message, settings.DEFAULT_FROM_EMAIL)
    
