"""
Telesthete KVM - Software KVM over IP
"""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="telesthete-kvm",
    version="0.3.0",
    author="Bake-Ware",
    author_email="jamixzol@gmail.com",
    description="Spatial window streaming and legacy KVM built on Telesthete",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Bake-Ware/telesthete-kvm",
    packages=find_packages(),
    package_data={"surfaces.adapters": ["kwin_tree.js"]},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "Topic :: System :: Hardware",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.11",
    install_requires=[
        "pynput>=1.8.1,<2",
        "websockets>=15,<18",
        "telesthete @ git+https://github.com/Bake-Ware/telesthete.git@b6290c7afabe14c0e81d4e9adfd5bcf498a251e5",
        "pyperclip>=1.8.2",
    ],
    extras_require={
        "test": ["pytest>=8", "pytest-asyncio>=0.24"],
        "spatial": [
            "PySide6>=6.8,<7",
            "av>=16,<18",
            "numpy>=1.26,<3",
            "zstandard>=0.23,<1",
            "windows-capture>=2.0.1,<3; platform_system == 'Windows'",
        ],
    },
    entry_points={
        "console_scripts": [
            "telesthete-kvm=kvm.kvm:main",
            "telesthete-surfaces=surfaces.__main__:main",
        ],
    },
)
