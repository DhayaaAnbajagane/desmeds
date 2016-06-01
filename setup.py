import os
import glob
from distutils.core import setup

scripts=[
    'desmeds-gen-all',
    'desmeds-gen-all-release',
    'desmeds-make-stubby-meds',
    'desmeds-make-stubby-meds-desdm',
    'desmeds-make-meds',
    'desmeds-rsync-meds-srcs',
    'desmeds-sync-meds-srcs',
]

scripts=[os.path.join('bin',s) for s in scripts]

setup(name="desmeds", 
      version="0.1.0",
      description="DES specific MEDS code",
      license = "GPL",
      author="Erin Scott Sheldon",
      author_email="erin.sheldon@gmail.com",
      scripts=scripts,
      packages=['desmeds'])
