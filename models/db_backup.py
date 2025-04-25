import os
import datetime
import time
import shutil
import json
import tempfile
import base64

from odoo import models, fields, api, tools, _
from odoo.exceptions import Warning, AccessDenied, UserError
import odoo

import logging
_logger = logging.getLogger(__name__)


class DbBackup(models.Model):
    _name = 'db.backup'
    _description = 'Backup configuration record'

    name = fields.Char(string='Nama')
    file_data = fields.Binary(string='File Download')
    file_name = fields.Char(string='Nama File')

    def _get_db_name(self):
        dbName = self._cr.dbname
        return dbName

    host = fields.Char('Host', required=True, default='localhost')
    port = fields.Char('Port', required=True, default=8069)
    name = fields.Char('Database', required=True, help='Database you want to schedule backups for',
                       default=_get_db_name)
    folder = fields.Char('Backup Directory', help='Absolute path for storing the backups', required=True,
                         default='/odoo/backups')
    backup_type = fields.Selection([('zip', 'Zip'), ('dump', 'Dump')], 'Backup Type', required=True, default='zip')
    autoremove = fields.Boolean('Auto. Remove Backups')
    days_to_keep = fields.Integer('Remove after x days', required=True)

    def action_backup_now(self):
        self.ensure_one()
        if not os.path.isdir(self.folder):
            os.makedirs(self.folder)

        bkp_file = '%s_%s.%s' % (time.strftime('%Y_%m_%d_%H_%M_%S'), self.name, self.backup_type)
        file_path = os.path.join(self.folder, bkp_file)

        try:
            with open(file_path, 'wb') as fp:
                self._take_dump(self.name, fp, 'db.backup', self.backup_type)

            with open(file_path, "rb") as f:
                file_data = f.read()

            self.write({
                'file_data': base64.b64encode(file_data),
                'file_name': bkp_file,
            })

            return {
                'type': 'ir.actions.act_url',
                'url': f'/web/content?model={self._name}&id={self.id}&field=file_data&filename_field=file_name&download=true',
                'target': 'self',
            }
        except Exception as error:
            _logger.error("Error while doing manual backup: %s", str(error))
            raise UserError("Gagal melakukan backup sekarang: %s" % str(error))

    @api.model
    def schedule_backup(self):
        conf_ids = self.search([])
        for rec in conf_ids:
            try:
                if not os.path.isdir(rec.folder):
                    os.makedirs(rec.folder)
            except:
                raise

            bkp_file = '%s_%s.%s' % (time.strftime('%Y_%m_%d_%H_%M_%S'), rec.name, rec.backup_type)
            file_path = os.path.join(rec.folder, bkp_file)
            fp = open(file_path, 'wb')
            try:
                fp = open(file_path, 'wb')
                self._take_dump(rec.name, fp, 'db.backup', rec.backup_type)
                fp.close()
            except Exception as error:
                _logger.debug("Couldn't backup database %s.", rec.name)
                _logger.debug("Exact error: %s", str(error))
                continue

            if rec.autoremove:
                directory = rec.folder
                for f in os.listdir(directory):
                    fullpath = os.path.join(directory, f)
                    if rec.name in fullpath:
                        timestamp = os.stat(fullpath).st_ctime
                        createtime = datetime.datetime.fromtimestamp(timestamp)
                        now = datetime.datetime.now()
                        delta = now - createtime
                        if delta.days >= rec.days_to_keep:
                            if os.path.isfile(fullpath) and (".dump" in f or '.zip' in f):
                                _logger.info("Delete local out-of-date file: %s", fullpath)
                                os.remove(fullpath)

    def _take_dump(self, db_name, stream, model, backup_format='zip'):
        cron_user_id = self.env.ref('auto_backup.backup_scheduler').user_id.id
        if self._name != 'db.backup' or (self.env.user.id != cron_user_id and not self.env.user.has_group('base.group_system')):
            _logger.error('Unauthorized database operation. Backups should only be available from the cron job.')
            raise AccessDenied()

        _logger.info('DUMP DB: %s format %s', db_name, backup_format)

        cmd = ['pg_dump', '--no-owner']
        cmd.append(db_name)

        if backup_format == 'zip':
            with tempfile.TemporaryDirectory() as dump_dir:
                filestore = odoo.tools.config.filestore(db_name)
                if os.path.exists(filestore):
                    shutil.copytree(filestore, os.path.join(dump_dir, 'filestore'))
                with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                    db = odoo.sql_db.db_connect(db_name)
                    with db.cursor() as cr:
                        json.dump(self._dump_db_manifest(cr), fh, indent=4)
                cmd.insert(-1, '--file=' + os.path.join(dump_dir, 'dump.sql'))
                odoo.tools.exec_pg_command(*cmd)
                if stream:
                    odoo.tools.osutil.zip_dir(dump_dir, stream, include_dir=False, fnct_sort=lambda file_name: file_name != 'dump.sql')
                else:
                    t = tempfile.TemporaryFile()
                    odoo.tools.osutil.zip_dir(dump_dir, t, include_dir=False, fnct_sort=lambda file_name: file_name != 'dump.sql')
                    t.seek(0)
                    return t
        else:
            cmd.insert(-1, '--format=c')
            stdin, stdout = odoo.tools.exec_pg_command_pipe(*cmd)
            if stream:
                shutil.copyfileobj(stdout, stream)
            else:
                return stdout

    def _dump_db_manifest(self, cr):
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'")
        modules = dict(cr.fetchall())
        manifest = {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }
        return manifest

    def action_generate_file(self):
        file_path = "/tmp/sftp_hasil_export.zip"
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            self.write({
                'file_data': base64.b64encode(file_data),
                'file_name': 'sftp_export.zip',
            })
        except Exception as e:
            raise UserError(f"Gagal membaca file: {e}")

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content?model={self._name}&id={self.id}&field=file_data&filename_field=file_name&download=true',
            'target': 'self',
        }
