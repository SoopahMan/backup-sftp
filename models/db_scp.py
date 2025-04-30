import os
import subprocess

from odoo import models, fields, api
from odoo.exceptions import UserError


class DbBackupSCPWizard(models.TransientModel):
    _name = 'db.backup.scp.wizard'
    _description = 'Wizard Kirim Backup via SCP'

    scp_user = fields.Char('SCP User', required=True)
    scp_host = fields.Char('SCP Host', required=True)
    scp_path = fields.Char('SCP Path', required=True)
    scp_private_key = fields.Char('Path ke Private Key', required=True)
    backup_ids = fields.Many2many('db.backup', string="Backup untuk dikirim")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        backup_ids = self.env.context.get('default_backup_ids', [])
        if backup_ids:
            res['backup_ids'] = [(6, 0, backup_ids)]
        return res

    def action_send(self):
        for backup in self.backup_ids:
            # Simulasi logika kirim SCP
            _logger.info(f"Kirim backup {backup.name} ke {self.scp_user}@{self.scp_host}:{self.scp_path}")