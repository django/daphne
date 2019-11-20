import os

from setuptools import find_packages, setup

from daphne import __version__

# We use the README as the long_description
readme_path = os.path.join(os.path.dirname(__file__), "README.rst")
with open(readme_path) as fp:
    long_description = fp.read()

setup(
    name="daphne",
    version=__version__,
    url="https://github.com/django/daphne",
    author="Django Software Foundation",
    author_email="foundation@djangoproject.com",
    description="Django ASGI (HTTP/WebSocket) server",
    long_description=long_description,
    license="BSD",
    zip_safe=False,
    package_dir={"twisted": "daphne/twisted"},
    packages=find_packages() + ["twisted.plugins"],
    include_package_data=True,
    install_requires=["twisted[tls]>=18.7", "autobahn>=0.18", "asgiref~=3.2"],
    setup_requires=["pytest-runner"],
    extras_require={
        "tests": ["hypothesis==4.23", "pytest~=3.10", "pytest-asyncio~=0.8"]
    },
    entry_points={
        "console_scripts": ["daphne = daphne.cli:CommandLineInterface.entrypoint"]
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Internet :: WWW/HTTP",
    ],
)
