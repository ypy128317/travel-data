"""第二层：商品主数据。"""

from .base import BaseGenerator
from .seed_importer import SeedImporter
from .validations import validate_layer2


class Layer2Generator(BaseGenerator):
    layer = 2
    layer_name = "商品主数据"

    def run(self) -> None:
        self.header()
        self.log_table_counts(SeedImporter().import_required_layer(self.layer))
        checks = validate_layer2()
        for check in checks:
            self.log(f"  [OK] validation: {check}")
