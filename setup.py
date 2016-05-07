import os
import sys
from setuptools import find_packages, setup
from daphne import __version__


# We use the README as the long_description
readme_path = os.path.join(os.path.dirname(__file__), "README.rst")


setup(
    name='daphne',
    version=__version__,
    url='http://www.djangoproject.com/',
    author='Django Software Foundation',
    author_email='foundation@djangoproject.com',
    description='Django ASGI (HTTP/WebSocket) server',
    long_description=open(readme_path).read(),
    license='BSD',
    zip_safe=False,
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'asgiref>=0.13',
        'twisted>=15.5',
        'autobahn>=0.12',
    ],
    entry_points={'console_scripts': [
        'daphne = daphne.cli:CommandLineInterface.entrypoint',
    ]},
)
