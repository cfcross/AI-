"""Install the versioned WorkBuddy skill; back up an existing installation first."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

def install(destination):
    source = ROOT / 'skills/market-stall-demo'
    destination = Path(destination).expanduser().resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Installation target must be separate from repository source')
    if destination.exists():
        backup = ROOT / 'data/skill-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copytree(destination, backup)
        print('Backup:', backup)
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '_user_meta.json'))
    print('Installed:', destination)
    print('Refresh WorkBuddy skills or restart WorkBuddy before the next task.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, default=Path.home() / '.workbuddy/skills/market-stall-demo')
    install(parser.parse_args().target)
