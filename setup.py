"""
Telesthete KVM - Software KVM over IP
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="telesthete-kvm",
    version="0.1.0",
    author="Bake-Ware",
    author_email="jamixzol@gmail.com",
    description="Software KVM (keyboard/video/mouse over IP) built on Telesthete",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Bake-Ware/telesthete-kvm",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "Topic :: System :: Hardware",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.10",
    install_requires=[
        "telesthete>=0.1.0",
        "pynput>=1.7.6",
        "pyperclip>=1.8.2",
    ],
    entry_points={
        "console_scripts": [
            "telesthete-kvm=kvm.kvm:main",
        ],
    },
)
